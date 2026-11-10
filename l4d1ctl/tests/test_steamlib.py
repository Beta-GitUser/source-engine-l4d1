import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l4d1ctl import steamlib
from l4d1ctl.tests.test_kv import APPMANIFEST, LIBRARY_FOLDERS_V2, LOGIN_USERS


def make_steam_root(root: Path, *, library: Path | None = None) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "steamapps" / "common" / "Left 4 Dead" / "left4dead").mkdir(parents=True, exist_ok=True)
    (root / "steamapps" / "appmanifest_500.acf").write_text(APPMANIFEST, encoding="utf-8")
    (root / "config" / "loginusers.vdf").write_text(LOGIN_USERS, encoding="utf-8")
    folders = LIBRARY_FOLDERS_V2.replace("/home/me/.steam/steam", str(root))
    if library is not None:
        folders = folders.replace("/mnt/games/SteamLibrary", str(library))
    (root / "steamapps" / "libraryfolders.vdf").write_text(folders, encoding="utf-8")
    return root


class DiscoveryTests(unittest.TestCase):
    def test_library_folders_include_root_and_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = Path(tmp) / "extra"
            extra.mkdir()
            root = make_steam_root(Path(tmp) / "steam", library=extra)
            folders = steamlib.library_folders(root)
            self.assertEqual(folders[0], root)
            self.assertIn(extra, folders)

    def test_find_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_steam_root(Path(tmp) / "steam")
            install = steamlib.find_install(500, steamlib.library_folders(root))
            self.assertIsNotNone(install)
            self.assertEqual(install.install_dir, root / "steamapps" / "common" / "Left 4 Dead")
            self.assertEqual(install.buildid, "9255")
            self.assertEqual(install.size_on_disk, 12884901888)
            self.assertFalse(install.looks_complete)

    def test_find_install_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "steam"
            (root / "steamapps").mkdir(parents=True)
            self.assertIsNone(steamlib.find_install(500, [root]))

    def test_discover_install_by_hand_copied_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "steam"
            (root / "steamapps").mkdir(parents=True)
            target = root / "steamapps" / "common" / "Left 4 Dead"
            target.mkdir(parents=True)
            (target / "gameinfo.txt").write_text("x", encoding="utf-8")
            install = steamlib.discover_install(steam_root=str(root))
            self.assertIsNotNone(install)
            self.assertEqual(install.manifest_path, None)

    def test_find_steam_root_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_steam_root(Path(tmp) / "steam")
            self.assertEqual(steamlib.find_steam_root(str(root)), root)
            self.assertIsNone(steamlib.find_steam_root(str(Path(tmp) / "nope")))

    def test_steam_root_candidates_include_env(self):
        candidates = steamlib.steam_root_candidates({"HOME": "/home/x", "L4D1_STEAM_ROOT": "/opt/steam"})
        self.assertEqual(candidates[0], Path("/opt/steam"))
        self.assertIn(Path("/sdcard/srceng"), candidates)


class IdentityTests(unittest.TestCase):
    def test_login_users_and_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_steam_root(Path(tmp) / "steam")
            users = steamlib.login_users(root)
            self.assertEqual(len(users), 2)
            current = steamlib.current_user(root)
            self.assertEqual(current.persona_name, "Zoey Fan")
            self.assertEqual(current.steamid64, 76561198000000001)

    def test_current_user_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_steam_root(Path(tmp) / "steam")
            user = steamlib.current_user(root, 76561197960265730)
            self.assertEqual(user.account_name, "oldaccount")

    def test_resolve_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_steam_root(Path(tmp) / "steam")
            identity = steamlib.resolve_identity(root)
            self.assertEqual(identity.display_name, "Zoey Fan")

    def test_resolve_identity_override_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_steam_root(Path(tmp) / "steam")
            identity = steamlib.resolve_identity(root, persona_name="Custom")
            self.assertEqual(identity.display_name, "Custom")

    def test_resolve_identity_without_steam(self):
        identity = steamlib.resolve_identity(None, steamid64=76561198000000001, persona_name="Nobody")
        self.assertEqual(identity.persona_name, "Nobody")

    def test_identity_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            identity = steamlib.Identity(steamid64=76561198000000001, persona_name="Zoey Fan",
                                         avatar_path="/tmp/a.png")
            identity.write(path)
            loaded = steamlib.Identity.from_file(path)
            self.assertEqual(loaded.persona_name, "Zoey Fan")
            self.assertEqual(loaded.steamid64, identity.steamid64)
            self.assertIsNone(steamlib.Identity.from_file(Path(tmp) / "missing.json"))

    def test_identity_from_file_rejects_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(steamlib.Identity.from_file(path))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class AvatarTests(unittest.TestCase):
    def _urlopen(self, payload):
        calls = []

        def opener(request, timeout=0):
            calls.append(request.full_url)
            return FakeResponse(json.dumps(payload).encode() if isinstance(payload, dict) else payload)

        return opener, calls

    def test_player_summaries(self):
        opener, calls = self._urlopen({
            "response": {"players": [{"steamid": "76561198000000001", "personaname": "Zoey Fan"}]}
        })
        result = steamlib.player_summaries([76561198000000001], "KEY", urlopen=opener)
        self.assertEqual(result[76561198000000001]["personaname"], "Zoey Fan")
        self.assertIn("key=KEY", calls[0])

    def test_player_summaries_http_error(self):
        def boom(request, timeout=0):
            raise OSError("no network")

        with self.assertRaises(steamlib.SteamWebApiError):
            steamlib.player_summaries([76561198000000001], "KEY", urlopen=boom)

    def test_download_avatar_requires_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(steamlib.SteamWebApiError):
                    steamlib.download_avatar(76561198000000001, Path(tmp) / "a.png")

    def test_download_avatar(self):
        payloads = [
            json.dumps({"response": {"players": [
                {"steamid": "76561198000000001", "avatarfull": "https://cdn/avatar.png"}]}}).encode(),
            b"\x89PNG\r\n\x1a\nfake",
        ]

        def opener(request, timeout=0):
            return FakeResponse(payloads.pop(0))

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "avatars" / "1.png"
            result = steamlib.download_avatar(76561198000000001, dest, api_key="KEY", urlopen=opener)
            self.assertEqual(result, dest)
            self.assertTrue(dest.read_bytes().startswith(b"\x89PNG"))

    def test_download_avatar_unknown_profile(self):
        def opener(request, timeout=0):
            return FakeResponse(json.dumps({"response": {"players": []}}).encode())

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(steamlib.SteamWebApiError):
                steamlib.download_avatar(76561198000000001, Path(tmp) / "a.png", api_key="KEY",
                                         urlopen=opener)


if __name__ == "__main__":
    unittest.main()
