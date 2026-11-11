"""Importing a raw depot download, not a Steam install.

The realistic phone workflow downloads depots with an external tool inside
proot/proot-distro and ends up with the *app* folder layout:

    <out>/bin/client.dll          <- Valve's PC binaries (depot 503)
    <out>/left4dead/gameinfo.txt
    <out>/left4dead/left4dead_dir.vpk
    <out>/platform/...

which is one level above the content folder our gamedir expects. A filtered
single-depot download may contain *only* ``bin/`` and no content folder at all.

These tests pin both shapes down, because the failure mode is silent: content
copied into the wrong level looks like a successful import and then fails at
launch with a missing gameinfo.
"""

import tempfile
import unittest
from pathlib import Path

from l4d1ctl import depot


def write(path: Path, payload: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def make_app_folder(root: Path) -> Path:
    """A depot-download-shaped tree: app folder with bin/ and left4dead/."""
    app = root / "Left 4 Dead"
    write(app / "bin" / "client.dll", b"MZ" + b"\0" * 62)
    write(app / "bin" / "server.dll", b"MZ" + b"\0" * 62)
    write(app / "left4dead" / "gameinfo.txt", b"GameInfo {}\n")
    write(app / "left4dead" / "left4dead_dir.vpk", b"VPK\x00")
    write(app / "left4dead" / "materials" / "vgui" / "x.vmt", b"patch")
    write(app / "platform" / "config" / "x.vdf", b"cfg")
    return app


class AppFolderImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.app = make_app_folder(self.root)
        self.target = self.root / "out" / "left4dead"

    def tearDown(self):
        self._tmp.cleanup()

    def test_content_folder_is_detected_inside_the_app_folder(self):
        """`Left 4 Dead/left4dead/gameinfo.txt` means: copy the inner folder."""
        stats = depot.copy_game_content(self.app, self.target)
        self.assertTrue((self.target / "gameinfo.txt").is_file())
        self.assertTrue((self.target / "left4dead_dir.vpk").is_file())
        self.assertEqual(stats.copied + stats.linked, 3)

    def test_pc_binaries_are_not_copied(self):
        depot.copy_game_content(self.app, self.target)
        self.assertFalse((self.target / "bin" / "client.dll").exists(),
                         "Valve's Windows DLLs are dead weight on a phone")

    def test_platform_is_a_sibling_of_the_content_folder(self):
        """``platform/`` sits next to ``left4dead/`` in the app folder, so it is
        not part of a content-folder import.

        Recorded as a fact rather than endorsed as a design: if the UI later
        turns up missing fonts or platform resources on device, this is the
        first place to look - the engine's search path does consult a platform
        directory.
        """
        depot.copy_game_content(self.app, self.target)
        self.assertFalse((self.target / "platform").exists())
        self.assertTrue((self.app / "platform" / "config" / "x.vdf").is_file(),
                        "the fixture still has it, one level up")

    def test_app_folder_detection_is_in_the_function_not_the_cli(self):
        self.assertEqual(depot.resolve_content_folder(self.app), self.app / "left4dead")
        self.assertEqual(depot.resolve_content_folder(self.app / "left4dead"),
                         self.app / "left4dead")
        partial = self.root / "partial"
        (partial / "bin").mkdir(parents=True)
        self.assertEqual(depot.resolve_content_folder(partial), partial)

    def test_verify_on_the_imported_gamedir_passes_the_content_checks(self):
        depot.copy_game_content(self.app, self.target)
        report = depot.verify_game_dir(self.target)
        messages = " | ".join(check.message for check in report.checks)
        self.assertIn("1 VPK pack", messages)
        # No module installed yet - that is a warning here, not a failure.
        self.assertNotIn("Unable to load client DLL", messages)

    def test_verify_on_the_app_folder_says_what_is_wrong(self):
        """Pointing the tools at the wrong level should be explained, not silent."""
        report = depot.verify_game_dir(self.app)
        failures = [check.message for check in report.checks if check.level == "fail"]
        self.assertTrue(any("gameinfo.txt" in message for message in failures))
        warnings = " ".join(check.message for check in report.checks if check.level == "warn")
        self.assertIn("client.dll", warnings)


class PartialDepotTests(unittest.TestCase):
    """`--depot 503 --file 'bin/.*\\.dll$'` yields bin/ and nothing else."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.partial = self.root / "depot503"
        write(self.partial / "bin" / "client.dll", b"MZ" + b"\0" * 62)
        write(self.partial / "bin" / "server.dll", b"MZ" + b"\0" * 62)
        self.target = self.root / "gamedir"

    def tearDown(self):
        self._tmp.cleanup()

    def test_mining_targets_are_where_they_are_expected(self):
        client = self.partial / "bin" / "client.dll"
        self.assertTrue(client.is_file())
        self.assertEqual(client.read_bytes()[:2], b"MZ")

    def test_import_creates_the_target_even_when_nothing_is_copied(self):
        """Otherwise the verifier reports "game directory does not exist", which
        reads like a crash rather than "you downloaded the wrong depot"."""
        stats = depot.copy_game_content(self.partial, self.target)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(stats.copied + stats.linked, 0)
        self.assertGreater(stats.skipped, 0)

    def test_importing_a_partial_tree_is_allowed_and_explained(self):
        depot.copy_game_content(self.partial, self.target)
        report = depot.verify_game_dir(self.target)
        messages = {level: [] for level in ("ok", "warn", "fail")}
        for check in report.checks:
            messages[check.level].append(check.message)
        self.assertTrue(any("gameinfo.txt missing" in message for message in messages["fail"]),
                        "a bin/-only import has no content, and that must be a failure")

    def test_verifying_the_partial_download_itself_calls_out_the_pc_binaries(self):
        """Pointed at the download rather than the target, the verifier should
        say these are PC binaries - that is the clue that it is depot 503."""
        report = depot.verify_game_dir(self.partial)
        warnings = " ".join(check.message for check in report.checks if check.level == "warn")
        self.assertIn("PC binaries", warnings)
        self.assertIn("client.dll", warnings)


class ImportCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.target = self.root / "gamedir"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        import subprocess, sys
        return subprocess.run([sys.executable, "-m", "l4d1ctl", "--gamedir", str(self.target), *args],
                              capture_output=True, text=True,
                              cwd=str(Path(__file__).resolve().parents[2]))

    def test_partial_import_explains_the_depot_mixup(self):
        partial = self.root / "depot503"
        write(partial / "bin" / "client.dll", b"MZ")
        result = self.run_cli("import", "--source", str(partial))
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing was imported", result.stderr)
        self.assertIn("503", result.stderr)
        self.assertIn("content depots", result.stderr)

    def test_app_folder_import_succeeds(self):
        app = make_app_folder(self.root)
        result = self.run_cli("import", "--source", str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / "left4dead_dir.vpk").is_file())
        self.assertNotIn("nothing was imported", result.stderr)


if __name__ == "__main__":
    unittest.main()
