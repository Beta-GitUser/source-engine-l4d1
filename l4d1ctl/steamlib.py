"""Steam library discovery and offline identity resolution.

What this module does:

* finds Steam installs and libraries (``libraryfolders.vdf``)
* finds an installed app by id (default: Left 4 Dead, 500)
* reads the *local* account list (``config/loginusers.vdf``) for persona name
  and SteamID64
* fetches the avatar through the public Steam Web API when the user supplies
  their own API key (``STEAM_WEBAPI_KEY``)

What it deliberately does **not** do: log in, mint auth session tickets, or
pretend a socket is a Steam client. Identity shown here is a local label; it
grants nothing and cannot be used to join retail servers.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from . import kv, steamid
from .paths import APPID, INSTALL_DIR_NAME

WEB_API_SUMMARIES = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"


class SteamLibraryError(RuntimeError):
    pass


class SteamWebApiError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Steam root / library discovery
# ---------------------------------------------------------------------------

def steam_root_candidates(environ: Optional[Dict[str, str]] = None) -> List[Path]:
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or Path.home())
    out: List[Path] = []

    for var in ("L4D1_STEAM_ROOT", "STEAM_ROOT", "STEAM_PATH"):
        if env.get(var):
            out.append(Path(env[var]).expanduser())

    system = platform.system()
    if system == "Windows":
        for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "ProgramFiles(x86)"):
            if env.get(var):
                out.append(Path(env[var]) / "Steam")
        if env.get("SystemDrive"):
            out.append(Path(env["SystemDrive"] + "\\") / "Steam")
    elif system == "Darwin":
        out.append(home / "Library" / "Application Support" / "Steam")
    else:
        out.extend(
            [
                home / ".steam" / "steam",
                home / ".steam" / "root",
                home / ".local" / "share" / "Steam",
                home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
                home / "Steam",
            ]
        )

    # Android / our own launcher layout: content copied to shared storage.
    out.append(Path("/sdcard/srceng"))

    seen = set()
    unique: List[Path] = []
    for path in out:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def find_steam_root(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_dir() else None
    for candidate in steam_root_candidates():
        if (candidate / "steamapps").is_dir() or (candidate / "config").is_dir():
            return candidate
    return None


def _library_paths_from_vdf(path: Path) -> List[Path]:
    """Parse both the legacy and the current ``libraryfolders.vdf`` shapes."""
    if not path.is_file():
        return []
    try:
        nodes = kv.load_file(path)
    except (OSError, kv.KVParseError):
        return []

    root = kv.find(nodes, "libraryfolders") or kv.find(nodes, "LibraryFolders")
    if root is None:
        # Some very old installs keep the paths at the top level.
        root = kv.KVNode("libraryfolders", children=list(nodes))

    found: List[Path] = []
    for child in root.children:
        if child.value and ("/" in child.value or "\\" in child.value or ":" in child.value):
            found.append(Path(child.value))
            continue
        if child.children:
            for key in ("path", "PATH", "Path"):
                node = child.find(key)
                if node and node.value:
                    found.append(Path(node.value))
                    break
    return found


def library_folders(steam_root: Path) -> List[Path]:
    """All libraries, with the Steam root itself first."""
    steam_root = Path(steam_root)
    libraries = [steam_root]
    candidates = [
        steam_root / "steamapps" / "libraryfolders.vdf",
        steam_root / "config" / "libraryfolders.vdf",
    ]
    for candidate in candidates:
        for path in _library_paths_from_vdf(candidate):
            expanded = Path(str(path).replace("\\\\", "\\"))
            if expanded not in libraries:
                libraries.append(expanded)
    return libraries


# ---------------------------------------------------------------------------
# Installed apps
# ---------------------------------------------------------------------------

@dataclass
class SteamInstall:
    appid: int
    library: Path
    install_dir: Path
    manifest_path: Optional[Path]
    name: str = ""
    state_flags: int = 0
    size_on_disk: int = 0
    buildid: str = ""

    @property
    def looks_complete(self) -> bool:
        return (self.install_dir / "gameinfo.txt").is_file() or any(
            self.install_dir.glob("*_dir.vpk")
        )


def _read_app_manifest(path: Path) -> kv.KVNode:
    nodes = kv.load_file(path)
    root = kv.find(nodes, "AppState")
    if root is None:
        raise SteamLibraryError(f"{path}: missing AppState section")
    return root


def find_install(appid: int = APPID, libraries: Optional[Iterable[Path]] = None,
                 name_hint: str = INSTALL_DIR_NAME) -> Optional[SteamInstall]:
    for library in libraries or []:
        steamapps = Path(library) / "steamapps"
        manifest = steamapps / f"appmanifest_{appid}.acf"
        if not manifest.is_file():
            continue
        try:
            state = _read_app_manifest(manifest)
        except (OSError, kv.KVParseError, SteamLibraryError):
            continue
        folder = state.get("installdir") or name_hint
        install_dir = steamapps / "common" / folder
        return SteamInstall(
            appid=appid,
            library=Path(library),
            install_dir=install_dir,
            manifest_path=manifest,
            name=state.get("name", ""),
            state_flags=state.get_int("StateFlags", 0),
            size_on_disk=state.get_int("SizeOnDisk", 0),
            buildid=state.get("buildid", ""),
        )
    return None


def discover_install(appid: int = APPID, steam_root: Optional[str] = None,
                     extra_libraries: Optional[Iterable[Path]] = None) -> Optional[SteamInstall]:
    """Find an install using an explicit root, auto-detection, then fallbacks."""
    libraries: List[Path] = []
    root = find_steam_root(steam_root)
    if root is not None:
        libraries.extend(library_folders(root))
    if extra_libraries:
        libraries.extend(Path(p) for p in extra_libraries)
    if not libraries:
        libraries.extend(steam_root_candidates())

    install = find_install(appid, libraries)
    if install is not None:
        return install

    # Last resort: a folder that was copied by hand (no manifest at all).
    for library in libraries:
        for candidate in (
            Path(library) / "steamapps" / "common" / INSTALL_DIR_NAME,
            Path(library) / "steamapps" / INSTALL_DIR_NAME,
            Path(library) / INSTALL_DIR_NAME,
        ):
            if (candidate / "gameinfo.txt").is_file() or any(candidate.glob("*_dir.vpk")):
                return SteamInstall(appid=appid, library=Path(library), install_dir=candidate,
                                    manifest_path=None, name=INSTALL_DIR_NAME)
    return None


# ---------------------------------------------------------------------------
# Local accounts (offline identity)
# ---------------------------------------------------------------------------

@dataclass
class SteamUser:
    steamid64: int
    account_name: str = ""
    persona_name: str = ""
    most_recent: bool = False
    timestamp: int = 0

    @property
    def display_name(self) -> str:
        return self.persona_name or self.account_name or "Player"


def login_users(steam_root: Optional[Path]) -> List[SteamUser]:
    if steam_root is None:
        return []
    candidates = [
        Path(steam_root) / "config" / "loginusers.vdf",
        Path(steam_root) / "config" / "LoginUsers.vdf",
    ]
    users: List[SteamUser] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            nodes = kv.load_file(path)
        except (OSError, kv.KVParseError):
            continue
        root = kv.find(nodes, "users") or kv.find(nodes, "Users")
        if root is None:
            continue
        for entry in root.children:
            sid = steamid.try_parse(entry.key)
            if sid is None:
                continue
            users.append(
                SteamUser(
                    steamid64=sid,
                    account_name=entry.get("AccountName"),
                    persona_name=entry.get("PersonaName"),
                    most_recent=entry.get_bool("MostRecent", False),
                    timestamp=entry.get_int("Timestamp", 0),
                )
            )
        break
    return users


def current_user(steam_root: Optional[Path], steamid64: Optional[int] = None) -> Optional[SteamUser]:
    users = login_users(steam_root)
    if not users:
        return None
    if steamid64 is not None:
        for user in users:
            if user.steamid64 == steamid64:
                return user
        return None
    for user in users:
        if user.most_recent:
            return user
    return max(users, key=lambda u: u.timestamp)


# ---------------------------------------------------------------------------
# Avatar via the public Web API (user's own key)
# ---------------------------------------------------------------------------

def _default_urlopen(request, timeout):  # pragma: no cover - thin wrapper
    return urllib.request.urlopen(request, timeout=timeout)


def player_summaries(steamids: Iterable[int], api_key: str, *,
                     timeout: float = 15.0,
                     urlopen: Optional[Callable] = None) -> Dict[int, dict]:
    """Return ``{steamid64: summary}`` using GetPlayerSummaries/v2."""
    ids = [str(int(s)) for s in steamids]
    if not ids:
        return {}
    query = urllib.parse.urlencode({"key": api_key, "steamids": ",".join(ids)})
    url = f"{WEB_API_SUMMARIES}?{query}"
    opener = urlopen or _default_urlopen
    request = urllib.request.Request(url, headers={"User-Agent": "l4d1ctl/1.0"})
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise SteamWebApiError(f"Steam Web API request failed: {exc}") from exc

    players = (payload.get("response") or {}).get("players") or []
    result: Dict[int, dict] = {}
    for player in players:
        try:
            sid = int(player.get("steamid", 0))
        except (TypeError, ValueError):
            continue
        result[sid] = player
    return result


def download_avatar(steamid64: int, destination: Path, api_key: Optional[str] = None, *,
                    size: str = "avatarfull",
                    timeout: float = 20.0,
                    urlopen: Optional[Callable] = None) -> Path:
    """Download one avatar PNG. Raises :class:`SteamWebApiError` on failure."""
    key = api_key or os.environ.get("STEAM_WEBAPI_KEY", "")
    if not key:
        raise SteamWebApiError(
            "no Steam Web API key: pass --api-key or set STEAM_WEBAPI_KEY "
            "(https://steamcommunity.com/dev/apikey). Without a key the launcher "
            "still works; it just shows a placeholder avatar."
        )
    summaries = player_summaries([steamid64], key, timeout=timeout, urlopen=urlopen)
    summary = summaries.get(int(steamid64))
    if not summary:
        raise SteamWebApiError(f"Steam Web API returned no profile for {steamid64}")
    url = summary.get(size) or summary.get("avatar") or summary.get("avatarmedium")
    if not url:
        raise SteamWebApiError(f"profile for {steamid64} has no avatar URL")

    opener = urlopen or _default_urlopen
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "l4d1ctl/1.0"})
    try:
        with opener(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SteamWebApiError(f"avatar download failed: {exc}") from exc
    if not data:
        raise SteamWebApiError("avatar download returned no data")
    destination.write_bytes(data)
    return destination


@dataclass
class Identity:
    steamid64: int
    persona_name: str
    account_name: str = ""
    avatar_path: Optional[str] = None
    source: str = "loginusers.vdf"

    @property
    def display_name(self) -> str:
        return self.persona_name or self.account_name or "Player"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def write(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.to_json() + "\n", encoding="utf-8")
        return destination

    @classmethod
    def from_file(cls, path: Path) -> Optional["Identity"]:
        path = Path(path)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = steamid.parse(data.get("steamid64", 0))
        except (OSError, ValueError):
            return None
        return cls(
            steamid64=sid,
            persona_name=data.get("persona_name", ""),
            account_name=data.get("account_name", ""),
            avatar_path=data.get("avatar_path"),
            source=data.get("source", "identity.json"),
        )


def resolve_identity(steam_root: Optional[Path], *, steamid64: Optional[int] = None,
                     persona_name: Optional[str] = None) -> Optional[Identity]:
    """Build an identity from local Steam files, with explicit overrides."""
    user = current_user(steam_root, steamid64) if steam_root else None

    if user is None and steamid64 is not None and persona_name:
        user = SteamUser(steamid64=steamid64, persona_name=persona_name)
    elif user is not None and persona_name:
        user.persona_name = persona_name
    if user is None:
        return steamid64 and Identity(steamid64=steamid64, persona_name=persona_name or "Player")

    return Identity(
        steamid64=user.steamid64,
        persona_name=user.persona_name,
        account_name=user.account_name,
        source="loginusers.vdf" if steam_root else "override",
    )


def describe_environment(steam_root: Optional[Path]) -> Dict[str, object]:
    """Small dict for ``l4d1ctl doctor`` and bug reports."""
    root = steam_root
    libraries = library_folders(root) if root else []
    users = login_users(root)
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "steam_root": str(root) if root else None,
        "libraries": [str(p) for p in libraries],
        "accounts": [u.display_name for u in users],
    }
