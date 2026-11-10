"""Filesystem layout used by the Left 4 Dead port tooling.

Layout next to the engine's game directory::

    <root>/left4dead/                 game content (depot 500), never tooling
      gameinfo.txt                    generated, mounts the mods below
      mods/<id>/mod.vdf               per-mod manifest, the source of truth
      mods/enabled/                   generated staging area, mounted by gameinfo
      cfg/l4d1_identity.cfg           generated, exec'd from the command line
      l4d1_port/                      all bookkeeping
        identity.json                 SteamID64 / persona name / avatar path
        avatars/<steamid64>.png
        gameinfo.generated            marker + hash of the generated gameinfo
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

APPID = 500
INSTALL_DIR_NAME = "Left 4 Dead"
GAME_DIR_NAME = "left4dead"


def default_gamedir() -> Path:
    """Best-effort default: env override, then the repo's ``game/`` folder."""
    env = os.environ.get("L4D1_GAMEDIR")
    if env:
        return Path(env).expanduser()
    # <repo>/l4d1ctl/paths.py -> repo root
    repo = Path(__file__).resolve().parents[1]
    return repo / "game" / GAME_DIR_NAME


@dataclass(frozen=True)
class PortPaths:
    gamedir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "gamedir", Path(self.gamedir).expanduser().resolve())

    # -- content -----------------------------------------------------------
    @property
    def gameinfo(self) -> Path:
        return self.gamedir / "gameinfo.txt"

    @property
    def cfg_dir(self) -> Path:
        return self.gamedir / "cfg"

    @property
    def identity_cfg(self) -> Path:
        return self.cfg_dir / "l4d1_identity.cfg"

    # -- mods --------------------------------------------------------------
    @property
    def mods_dir(self) -> Path:
        return self.gamedir / "mods"

    @property
    def staged_dir(self) -> Path:
        return self.mods_dir / "enabled"

    @property
    def mods_stamp(self) -> Path:
        return self.state_dir / "mods.staged.json"

    # -- state -------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return self.gamedir / "l4d1_port"

    @property
    def identity_json(self) -> Path:
        return self.state_dir / "identity.json"

    @property
    def avatars_dir(self) -> Path:
        return self.state_dir / "avatars"

    @property
    def gameinfo_marker(self) -> Path:
        return self.state_dir / "gameinfo.generated"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    # -- helpers -----------------------------------------------------------
    def ensure_state(self) -> None:
        for directory in (self.state_dir, self.avatars_dir, self.logs_dir, self.cfg_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def avatar_path(self, steamid64: int) -> Path:
        return self.avatars_dir / f"{steamid64}.png"


def resolve_gamedir(value: Optional[str]) -> PortPaths:
    return PortPaths(Path(value) if value else default_gamedir())
