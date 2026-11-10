import json
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from l4d1ctl import cli, preflight, selftest, vpk


def run(argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class SelftestRunTests(unittest.TestCase):
    def test_every_stage_passes(self):
        report = selftest.run()
        self.assertTrue(report.ok, report.render())
        self.assertEqual(len(report.stages), len(selftest.STAGES))
        self.assertIn("selftest passed", report.headline())

    def test_stage_names_are_unique_and_ordered(self):
        names = [name for name, _ in selftest.STAGES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[0], "paths")
        self.assertIn("preflight", names)

    def test_render_marks_each_stage(self):
        report = selftest.run(stages=["paths", "gameinfo"])
        text = report.render()
        self.assertIn("[PASS] paths", text)
        self.assertIn("[PASS] gameinfo", text)
        self.assertNotIn("vpk", text)

    def test_keep_directory_is_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kept"
            report = selftest.run(root)
            self.assertTrue(report.ok, report.render())
            self.assertEqual(report.root, root)
            self.assertTrue((root / "left4dead" / "gameinfo.txt").is_file())
            self.assertTrue((root / "left4dead" / "bin" / "client.so").is_file())

            # A second run over the same folder must still pass: the stages are
            # idempotent, which is what makes `--keep` useful for debugging.
            again = selftest.run(root)
            self.assertTrue(again.ok, again.render())

    def test_failure_is_reported_not_raised(self):
        def explode(_ctx):
            raise RuntimeError("boom")

        with mock.patch.object(selftest, "STAGES", [("exploding", explode)]):
            report = selftest.run(stages=["exploding"])
        self.assertFalse(report.ok)
        self.assertIn("RuntimeError: boom", report.stages[0].error)
        self.assertIn("[FAIL] exploding", report.render())
        self.assertIn("FAILED", report.headline())

    def test_json_contract(self):
        report = selftest.run(stages=["paths"])
        payload = json.loads(json.dumps(report.to_dict()))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stages"][0]["name"], "paths")

    def test_only_selected_stages_run(self):
        report = selftest.run(stages=["vpk"])
        self.assertEqual([stage.name for stage in report.stages], ["vpk"])
        self.assertTrue(report.ok, report.render())


class FixtureTests(unittest.TestCase):
    def test_fake_module_claims_the_host_architecture(self):
        blob = selftest.fake_module("client")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.so"
            path.write_bytes(blob)
            info = __import__("l4d1ctl.gamemod", fromlist=["inspect_binary"]).inspect_binary(path)
            self.assertEqual(info.format, "ELF")
            self.assertEqual(info.bits, preflight.host_bits())
            self.assertEqual(info.machine, preflight.host_machine())
            self.assertEqual(info.kind, "shared object")

    def test_fake_module_carries_interface_versions(self):
        client = selftest.fake_module("client")
        server = selftest.fake_module("server")
        self.assertIn(b"VClient017", client)
        self.assertIn(b"ServerGameDLL010", server)

    def test_fake_engine_is_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = selftest.fake_engine(Path(tmp) / "hl2_linux")
            self.assertTrue(engine.stat().st_mode & 0o111)
            self.assertTrue(engine.read_text().startswith("#!/bin/sh"))

    def test_sample_vpk_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = selftest.sample_vpk(Path(tmp))
            pack = vpk.open_vpk(target)
            self.assertEqual(len(pack), len(selftest._SAMPLE_FILES))
            self.assertEqual(pack.verify(), [])


class SelftestCommandTests(unittest.TestCase):
    def test_cli_selftest_passes(self):
        code, out, _ = run(["selftest"])
        self.assertEqual(code, 0, out)
        self.assertIn("selftest passed", out)

    def test_cli_selftest_json(self):
        code, out, _ = run(["selftest", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])

    def test_cli_selftest_stage_filter(self):
        code, out, _ = run(["selftest", "--stage", "gameinfo"])
        self.assertEqual(code, 0)
        self.assertIn("gameinfo", out)
        self.assertNotIn("preflight", out)

    def test_cli_selftest_failure_exit_code(self):
        with mock.patch.object(selftest, "STAGES", [("always_fails", lambda _c: 1 / 0)]):
            code, out, _ = run(["selftest"])
        self.assertEqual(code, 1)
        self.assertIn("ZeroDivisionError", out)

    def test_cli_selftest_keep(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "kept"
            code, out, _ = run(["selftest", "--keep", str(target), "--stage", "paths"])
            self.assertEqual(code, 0, out)
            self.assertTrue(target.is_dir())


class StructTests(unittest.TestCase):
    def test_stage_defaults_to_ok(self):
        stage = selftest.Stage(name="x", run=lambda _ctx: "")
        self.assertTrue(stage.ok)
        self.assertEqual(stage.error, "")

    def test_report_headline_counts_failures(self):
        report = selftest.Report(root=Path("/tmp"))
        report.stages = [selftest.Stage("a", lambda _c: ""),]
        report.stages.append(selftest.Stage("b", lambda _c: "", ok=False, error="nope"))
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failures), 1)
        self.assertIn("1 of 2", report.headline())

    def test_elf_header_shape(self):
        blob = selftest.fake_module("client")
        self.assertEqual(blob[:4], b"\x7fELF")
        self.assertIn(blob[4], (1, 2))
        struct.unpack_from("<H", blob, 16)  # e_type parses
        self.assertEqual(struct.unpack_from("<H", blob, 16)[0], 3)


if __name__ == "__main__":
    unittest.main()
