"""scripts/vpk-list.py is the reader we reach for when a name "is not in the pack".

Its job is to make three different questions separable: does the tree have a
folder at all, what does it spell the entry names, and are the bytes the entries
point at actually present. The tests below pin each view against a pack built with
known contents, so a claim like "no scripts/ folder" has evidence behind it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import vpk

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vpk-list.py"


class ListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = self.root / "left4dead"
        self.game.mkdir()

    def build(self, files, split=False, version=1):
        builder = vpk.build_vpk_v2 if version == 2 else vpk.build_vpk_v1
        return builder(self.game / "pak01_dir.vpk", files, split=split)

    def run_script(self, *argv):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, argv)],
                              capture_output=True, text=True)

    def test_the_summary_counts_what_the_tree_says(self):
        self.build({"scripts/surfaceproperties.txt": b"x" * 40,
                    "materials/models/a.vmt": b"y" * 10,
                    "materials/b.vmt": b"z"})
        result = self.run_script(self.game / "pak01_dir.vpk")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("entries:    3", result.stdout)
        self.assertIn("folders:    3", result.stdout)
        self.assertIn("data:       3/3 entries point at bytes that exist", result.stdout)

    def test_a_pack_without_a_scripts_folder_says_so_by_omission(self):
        """The claim the surface tool makes has to be readable here: no folder."""
        self.build({"materials/a.vmt": b"x"})
        result = self.run_script(self.game / "pak01_dir.vpk", "--folders")
        self.assertEqual(result.returncode, 0)
        self.assertIn("materials", result.stdout)
        self.assertNotIn("scripts", result.stdout)

    def test_filter_lists_names_as_spelled_with_their_sizes(self):
        self.build({"scripts/surfaceproperties_left4dead.txt": b"x" * 31817})
        result = self.run_script(self.game / "pak01_dir.vpk", "--filter",
                                 "surfaceproperties")
        self.assertEqual(result.returncode, 0)
        self.assertIn("scripts/surfaceproperties_left4dead.txt", result.stdout)
        self.assertIn("31817", result.stdout)

    def test_filter_that_matches_nothing_exits_one_and_points_at_strings(self):
        self.build({"materials/a.vmt": b"x"})
        result = self.run_script(self.game / "pak01_dir.vpk", "--filter", "surface")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing by that name", result.stdout)
        self.assertIn("--strings", result.stdout)

    def test_strings_shows_the_tree_stores_extension_folder_and_name_apart(self):
        """A path is never a single string in the tree, which is why a grep for the
        full path can miss a file that is there."""
        self.build({"scripts/surfaceproperties.txt": b"x"})
        result = self.run_script(self.game / "pak01_dir.vpk", "--strings", "20")
        self.assertIn("  txt", result.stdout)
        self.assertIn("  scripts", result.stdout)
        self.assertIn("  surfaceproperties", result.stdout)
        self.assertNotIn("  scripts/surfaceproperties.txt", result.stdout)

    def test_extract_writes_one_entry_out_byte_for_byte(self):
        payload = b'"concrete"\n{\n\t"density" "2300"\n}\n'
        self.build({"scripts/surfaceproperties.txt": payload, "materials/a.vmt": b"y"})
        target = self.root / "out.txt"
        result = self.run_script(self.game / "pak01_dir.vpk", "--extract",
                                 "scripts/surfaceproperties.txt", "--out", target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(target.read_bytes(), payload)

    def test_extract_follows_the_entry_into_its_part_file(self):
        payload = b"from a part file" * 10
        self.build({"scripts/big.txt": payload}, split=True)
        target = self.root / "part.txt"
        result = self.run_script(self.game / "pak01_dir.vpk", "--extract",
                                 "scripts/big.txt", "--out", target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(target.read_bytes(), payload)

    def test_extract_of_a_name_that_is_not_there_exits_one(self):
        self.build({"materials/a.vmt": b"y"})
        result = self.run_script(self.game / "pak01_dir.vpk", "--extract", "scripts/nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not in", result.stderr)

    def test_write_dumps_every_entry_with_its_size(self):
        self.build({"scripts/a.txt": b"x" * 7, "materials/b.vmt": b"y"})
        target = self.root / "entries.txt"
        result = self.run_script(self.game / "pak01_dir.vpk", "--write", target)
        self.assertEqual(result.returncode, 0)
        lines = target.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("scripts/a.txt", "\n".join(lines))

    def test_json_is_parseable_and_carries_the_folder_histogram(self):
        self.build({"scripts/a.txt": b"x", "materials/b.vmt": b"y"})
        result = self.run_script(self.game / "pak01_dir.vpk", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["entries"], 2)
        self.assertEqual(payload["folders"]["scripts"], 1)

    def test_a_version_2_pack_lists_all_of_its_entries(self):
        """Regression: reading tree_size from after the v2 header truncated the
        index, which is what made six real packs look like they had no scripts/."""
        self.build({f"scripts/s{i}.txt": b"x" for i in range(30)}
                   | {"materials/a.vmt": b"y"}, version=2)
        folders = self.run_script(self.game / "pak01_dir.vpk", "--folders")
        self.assertEqual(folders.returncode, 0, folders.stdout + folders.stderr)
        self.assertIn("30  scripts", folders.stdout)
        self.assertIn("1  materials", folders.stdout)
        summary = self.run_script(self.game / "pak01_dir.vpk")
        self.assertIn("entries:    31", summary.stdout)

    def test_a_data_part_is_refused_by_name(self):
        self.build({"materials/a.vmt": b"x"}, split=True)
        result = self.run_script(self.game / "pak01_000.vpk")
        self.assertEqual(result.returncode, 2)
        self.assertIn("data part", result.stderr)
        self.assertIn("pak01_dir.vpk", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_file_that_is_not_a_vpk_says_which_signature_it_saw(self):
        bogus = self.game / "pak01_dir.vpk"
        bogus.write_bytes(b"VTF\0" + b"\0" * 64)
        result = self.run_script(bogus)
        self.assertEqual(result.returncode, 2)
        self.assertIn("0x", result.stderr)

    def test_missing_data_is_counted_not_assumed(self):
        self.build({"materials/a.vmt": b"x" * 100}, split=True)
        (self.game / "pak01_000.vpk").unlink()
        result = self.run_script(self.game / "pak01_dir.vpk")
        self.assertIn("0/1 entries point at bytes that exist", result.stdout)
        self.assertIn("pak01_000.vpk is missing", result.stdout)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("left4dead", "hl2", "platform"):
            (self.root / name).mkdir()

    def run_script(self, *argv):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, argv)],
                              capture_output=True, text=True)

    def test_it_lists_every_pack_and_which_of_them_has_scripts(self):
        vpk.build_vpk_v1(self.root / "left4dead" / "pak01_dir.vpk",
                         {"materials/a.vmt": b"x"})
        vpk.build_vpk_v1(self.root / "hl2" / "hl2_misc_dir.vpk",
                         {"scripts/surfaceproperties.txt": b"x"})
        result = self.run_script(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hl2/hl2_misc_dir.vpk: vpk v1, 1 entries, 1 under scripts/",
                      result.stdout)
        self.assertIn("left4dead/pak01_dir.vpk: vpk v1, 1 entries, no scripts/ folder",
                      result.stdout)

    def test_filter_across_packs_finds_the_file_in_the_one_that_has_it(self):
        vpk.build_vpk_v1(self.root / "left4dead" / "pak01_dir.vpk",
                         {"materials/a.vmt": b"x"})
        vpk.build_vpk_v1(self.root / "hl2" / "hl2_misc_dir.vpk",
                         {"scripts/surfaceproperties_manifest.txt": b"x" * 551})
        result = self.run_script(self.root, "--filter", "surfaceproperties")
        self.assertEqual(result.returncode, 0)
        self.assertIn("scripts/surfaceproperties_manifest.txt", result.stdout)
        self.assertIn("551", result.stdout)

    def test_a_root_with_no_packs_exits_two(self):
        result = self.run_script(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no *_dir.vpk", result.stdout)


if __name__ == "__main__":
    unittest.main()
