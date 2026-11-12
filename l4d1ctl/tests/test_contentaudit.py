"""The boot-asset audit has one job: distinguish "not in the content" from
"not where the engine looks".

A missing file and an unmounted file produce the same engine log line, and the
fixes are opposites. So these tests pin the audit's answer to where each file
actually was found, across both storage forms the content uses - loose files and
``*_dir.vpk`` archives - including archives nested in subdirectories like
``platform/``.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import contentaudit, vpk

ALL_FOUR = [path for path, _, _ in contentaudit.BOOT_ASSETS]


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="content-audit-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        self.game = self.dir / "left4dead"
        self.game.mkdir()

    def findings(self):
        return {f.path: f for f in contentaudit.audit(self.dir)}

    def test_everything_in_one_archive(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {path: b"vtf" for path in ALL_FOUR}, split=True)
        findings = self.findings()
        self.assertTrue(all(f.found for f in findings.values()))
        self.assertEqual({f.found_in for f in findings.values()}, {"pak01_dir.vpk"})

    def test_loose_files_count_and_are_named_as_such(self):
        for path in ALL_FOUR:
            target = self.game / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"vtf")
        findings = {f.path: f for f in contentaudit.audit(self.game)}
        self.assertTrue(all(f.found for f in findings.values()))
        self.assertEqual({f.found_in for f in findings.values()}, {"loose"})

    def test_a_source_root_finds_the_game_subdirectory(self):
        """…/srceng with left4dead/ inside it must work as well as …/left4dead."""
        target = self.game / "materials" / "dev" / "identitylightwarp.vtf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"vtf")
        findings = {f.path: f for f in contentaudit.audit(self.dir)}
        self.assertTrue(findings["materials/dev/identitylightwarp.vtf"].found)

    def test_a_nested_platform_archive_is_searched(self):
        platform = self.dir / "platform"
        platform.mkdir()
        vpk.build_vpk_v1(platform / "platform_misc_dir.vpk", {
            "materials/dev/identitylightwarp.vtf": b"vtf"}, split=True)
        finding = self.findings()["materials/dev/identitylightwarp.vtf"]
        self.assertTrue(finding.found)
        self.assertEqual(finding.found_in, "platform_misc_dir.vpk")

    def test_absence_is_reported_not_raised(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"materials/dev/identitylightwarp.vtf": b"vtf"}, split=True)
        summary = contentaudit.summarise(contentaudit.audit(self.dir))
        self.assertFalse(summary["ok"])
        self.assertEqual(sorted(summary["missing"]),
                         sorted(p for p in ALL_FOUR if p != "materials/dev/identitylightwarp.vtf"))

    def test_case_does_not_matter(self):
        """Valve ships mixed case and Android's filesystem does not forgive it."""
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"Materials/Dev/IdentityLightWarp.VTF": b"vtf"}, split=True)
        self.assertTrue(self.findings()["materials/dev/identitylightwarp.vtf"].found)

    def test_none_of_the_four_is_described_as_fatal(self):
        for _, _, cost in contentaudit.BOOT_ASSETS:
            self.assertNotIn("fatal", cost.replace("not fatal", ""),
                             "the audit must not imply these stop a map loading")

    def test_every_asset_explains_its_cost(self):
        for path, description, cost in contentaudit.BOOT_ASSETS:
            self.assertTrue(path.startswith("materials/"), path)
            self.assertTrue(description and cost, path)


class CommandTests(unittest.TestCase):
    def test_json_output_is_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "left4dead").mkdir()
            result = subprocess.run(
                [sys.executable, "-m", "l4d1ctl", "content", "--root", tmp, "--json"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["checked"], len(ALL_FOUR))
            self.assertEqual(sorted(payload["missing"]), sorted(ALL_FOUR))

    def test_missing_directory_is_an_error_not_a_traceback(self):
        result = subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "content", "--root", "/nonexistent-l4d1"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no such directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
