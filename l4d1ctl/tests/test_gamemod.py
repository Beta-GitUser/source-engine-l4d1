import json
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from l4d1ctl import cli, gamemod
from l4d1ctl.paths import PortPaths


def fake_elf(bits: int = 64, machine: int = 183, etype: int = 3) -> bytes:
    ident = b"\x7fELF" + bytes([2 if bits == 64 else 1, 1, 1]) + b"\x00" * 9
    return ident + struct.pack("<HH", etype, machine) + b"\x00" * 64


def make_build(root: Path, *, client=True, server=True, arch=(64, 183)) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if client:
        (root / "client.so").write_bytes(fake_elf(*arch))
    if server:
        (root / "server.so").write_bytes(fake_elf(*arch))
    (root / "GameUI.so").write_bytes(fake_elf(*arch))
    return root


def paths_for(root: Path) -> PortPaths:
    return PortPaths(root / "left4dead")


class InspectTests(unittest.TestCase):
    def test_elf_arm64(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.so"
            path.write_bytes(fake_elf(64, 183))
            info = gamemod.inspect_binary(path)
            self.assertEqual(info.format, "ELF")
            self.assertEqual(info.bits, 64)
            self.assertEqual(info.machine, "AArch64")
            self.assertEqual(info.kind, "shared object")
            self.assertIn("AArch64", info.summary)

    def test_elf_x86_32(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.so"
            path.write_bytes(fake_elf(32, 3))
            info = gamemod.inspect_binary(path)
            self.assertEqual((info.bits, info.machine), (32, "x86"))

    def test_pe_and_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            pe = Path(tmp) / "client.dll"
            pe.write_bytes(b"MZ" + b"\x00" * 64)
            self.assertEqual(gamemod.inspect_binary(pe).format, "PE")

            junk = Path(tmp) / "junk.so"
            junk.write_bytes(b"not a binary")
            self.assertEqual(gamemod.inspect_binary(junk).format, "unknown")
            self.assertEqual(gamemod.inspect_binary(Path(tmp) / "missing.so").format, "unknown")

    def test_module_name_for(self):
        cases = {
            "client.so": "client",
            "libclient.so": "client",
            "server.so": "server",
            "libserver.so": "server",
            "server.dll": "server",
            "gameui.so": "gameui",
            "client.so.1": "client",
            "Client.SO": "client",
        }
        for filename, expected in cases.items():
            self.assertEqual(gamemod.module_name_for(filename), expected, filename)
        for filename in ("data.so", "client.txt", "server", "libsteam_api.so"):
            self.assertIsNone(gamemod.module_name_for(filename), filename)


class ScanTests(unittest.TestCase):
    def test_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            found = gamemod.scan_modules(build)
            self.assertEqual(sorted(found), ["client", "gameui", "server"])
            self.assertEqual(found["client"].path.name, "client.so")

    def test_scan_empty_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gamemod.scan_modules(Path(tmp)), {})
            self.assertEqual(gamemod.scan_modules(Path(tmp) / "nope"), {})


class InstallTests(unittest.TestCase):
    def test_install_both_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            paths = paths_for(Path(tmp))
            result = gamemod.install_modules(build, paths)
            self.assertEqual(sorted(result.installed), ["GameUI.so", "client.so", "server.so"])
            self.assertTrue((paths.bindir / "client.so").is_file())
            self.assertTrue((paths.bindir / "server.so").is_file())

    def test_install_missing_server_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin", server=False)
            result = gamemod.install_modules(build, paths_for(Path(tmp)))
            self.assertTrue(any("will not start" in w for w in result.warnings))

    def test_install_skips_unknown_unless_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            (build / "steam_api.so").write_bytes(fake_elf())
            paths = paths_for(Path(tmp))
            result = gamemod.install_modules(build, paths)
            self.assertIn("steam_api.so", result.skipped)
            self.assertFalse((paths.bindir / "steam_api.so").exists())

            result = gamemod.install_modules(build, paths, install_all=True)
            self.assertTrue((paths.bindir / "steam_api.so").is_file())

    def test_install_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            paths = paths_for(Path(tmp))
            gamemod.install_modules(build, paths)
            (paths.bindir / "client.so").write_bytes(b"older build")

            result = gamemod.install_modules(build, paths)
            self.assertIn("client.so", result.skipped)
            self.assertEqual((paths.bindir / "client.so").read_bytes(), b"older build")

            gamemod.install_modules(build, paths, force=True)
            self.assertEqual((paths.bindir / "client.so").read_bytes(), build.joinpath("client.so").read_bytes())

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            paths = paths_for(Path(tmp))
            gamemod.install_modules(build, paths)
            second = gamemod.install_modules(build, paths)
            self.assertEqual(second.installed, [])
            self.assertTrue(second.unchanged)

    def test_install_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            paths = paths_for(Path(tmp))
            result = gamemod.install_modules(build, paths, dry_run=True)
            self.assertTrue(result.installed)
            self.assertFalse(paths.bindir.exists())

    def test_install_link_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            paths = paths_for(Path(tmp))
            gamemod.install_modules(build, paths, link=True)
            import os

            self.assertTrue(os.path.samefile(build / "client.so", paths.bindir / "client.so"))

    def test_install_source_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(gamemod.ModuleError):
                gamemod.install_modules(Path(tmp) / "nope", paths_for(Path(tmp)))

    def test_install_empty_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            with self.assertRaises(gamemod.ModuleError):
                gamemod.install_modules(empty, paths_for(Path(tmp)))


class StatusTests(unittest.TestCase):
    def test_status_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = gamemod.status(paths_for(Path(tmp)))
            self.assertFalse(result.ok)
            self.assertEqual(result.missing, ["client", "server"])
            self.assertTrue(result.warnings)

    def test_status_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = paths_for(Path(tmp))
            gamemod.install_modules(make_build(Path(tmp) / "bin"), paths)
            result = gamemod.status(paths)
            self.assertTrue(result.ok)
            self.assertEqual(result.missing, [])
            self.assertEqual(result.modules["client"].info.bits, 64)

    def test_status_flags_bad_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = paths_for(Path(tmp))
            paths.bindir.mkdir(parents=True)
            (paths.bindir / "client.so").write_bytes(b"garbage")
            (paths.bindir / "server.so").write_bytes(fake_elf())
            result = gamemod.status(paths)
            self.assertTrue(result.ok)  # files exist, but they are suspicious
            self.assertTrue(any("not a recognisable binary" in w for w in result.warnings))

    def test_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = paths_for(Path(tmp))
            gamemod.install_modules(make_build(Path(tmp) / "bin"), paths)
            removed = gamemod.remove_modules(paths)
            self.assertEqual(sorted(removed), ["GameUI.so", "client.so", "server.so"])
            self.assertFalse((paths.bindir / "server.so").exists())

    def test_describe_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = make_build(Path(tmp) / "bin")
            lines = gamemod.describe_source(build)
            self.assertEqual(len(lines), 3)
            self.assertTrue(any("AArch64" in line for line in lines))


class CliTests(unittest.TestCase):
    def run_cli(self, argv):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_module_install_status_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build = make_build(root / "bin")
            gamedir = root / "left4dead"

            code, out, _ = self.run_cli(["--gamedir", str(gamedir), "module", "install",
                                         "--from", str(build)])
            self.assertEqual(code, 0, out)
            self.assertIn("installed client.so", out)

            code, out, _ = self.run_cli(["--gamedir", str(gamedir), "module", "status", "--json"])
            self.assertEqual(code, 0, out)
            payload = json.loads(out)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["modules"]["server"]["machine"], "AArch64")

            code, out, _ = self.run_cli(["--gamedir", str(gamedir), "module", "remove"])
            self.assertEqual(code, 0, out)
            self.assertIn("removed", out)

    def test_module_status_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            gamedir = Path(tmp) / "left4dead"
            code, out, _ = self.run_cli(["--gamedir", str(gamedir), "module", "status"])
            self.assertEqual(code, 1)
            self.assertIn("missing: client", out)

    def test_module_install_needs_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_cli(["--gamedir", str(Path(tmp) / "g"), "module", "install"])
            self.assertEqual(code, 2)
            self.assertIn("--from", err)

    def test_module_install_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gamedir = root / "left4dead"
            code, out, _ = self.run_cli(["--gamedir", str(gamedir), "module", "install",
                                         "--from", str(make_build(root / "bin")), "--dry-run"])
            self.assertEqual(code, 0, out)
            self.assertIn("would install", out)
            self.assertFalse((gamedir / "bin").exists())


if __name__ == "__main__":
    unittest.main()
