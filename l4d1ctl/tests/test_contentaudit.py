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


class FindTests(unittest.TestCase):
    """`find` answers "is it there" and "is it spelled that way", which are
    different questions once the content is on a case-sensitive filesystem."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "left4dead"
        self.game.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_match_in_a_pack_is_exact(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"scripts/surfaceproperties_manifest.txt": b"x"})
        hits = contentaudit.find(self.game, "scripts/surfaceproperties_manifest.txt")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].exact)
        self.assertEqual(hits[0].source, "pak01_dir.vpk")

    def test_a_case_difference_is_reported_as_a_near_miss(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"scripts/SurfaceProperties_Manifest.txt": b"x"})
        hits = contentaudit.find(self.game, "scripts/surfaceproperties_manifest.txt")
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0].exact)
        self.assertEqual(hits[0].path, "scripts/SurfaceProperties_Manifest.txt")

    def test_loose_files_count_and_keep_their_spelling(self):
        (self.game / "scripts").mkdir()
        (self.game / "scripts" / "surfaceproperties_left4dead.txt").write_bytes(b"x")
        hits = contentaudit.find(self.game, "scripts/surfaceproperties_left4dead.txt")
        self.assertTrue(any(hit.exact and hit.source == "loose" for hit in hits))

    def test_a_game_directory_under_a_root_is_searched(self):
        (self.game / "scripts").mkdir()
        (self.game / "scripts" / "surfaceproperties.txt").write_bytes(b"x")
        hits = contentaudit.find(self.root, "scripts/surfaceproperties.txt")
        self.assertTrue(hits, "the left4dead/ prefix should not hide the file")

    def test_absent_is_an_empty_list_not_an_exception(self):
        self.assertEqual(contentaudit.find(self.game, "scripts/nope.txt"), [])

    def _find(self, wanted):
        return subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "find", wanted, "--root", str(self.game)],
            capture_output=True, text=True)

    def test_the_command_exits_by_what_it_found(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"scripts/SurfaceProperties_Manifest.txt": b"x"})
        result = self._find("scripts/surfaceproperties_manifest.txt")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("[case]", result.stdout)
        self.assertEqual(self._find("scripts/missing.txt").returncode, 2)

    def test_cat_prints_what_is_in_the_file(self):
        manifest = b'surfaceproperties_manifest\n{\n\t"file"\t"scripts/surfaceproperties_left4dead.txt"\n}\n'
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"scripts/surfaceproperties_manifest.txt": manifest})
        result = subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "find",
             "scripts/surfaceproperties_manifest.txt", "--root", str(self.game), "--cat", "4"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("surfaceproperties_manifest", result.stdout)
        self.assertIn("surfaceproperties_left4dead.txt", result.stdout)

    def test_read_hit_reads_from_a_pack_and_from_disk(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk", {"scripts/a.txt": b"pack"})
        hits = contentaudit.find(self.game, "scripts/a.txt")
        self.assertEqual(contentaudit.read_hit(self.game, hits[0]), b"pack")

        (self.game / "scripts").mkdir()
        (self.game / "scripts" / "b.txt").write_bytes(b"disk")
        hits = contentaudit.find(self.game, "scripts/b.txt")
        loose = [hit for hit in hits if hit.source == "loose"][0]
        self.assertEqual(contentaudit.read_hit(self.game, loose), b"disk")

    def test_an_exact_match_exits_zero(self):
        vpk.build_vpk_v1(self.game / "pak01_dir.vpk",
                         {"scripts/surfaceproperties_manifest.txt": b"x"})
        result = self._find("scripts/surfaceproperties_manifest.txt")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[exact]", result.stdout)


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
