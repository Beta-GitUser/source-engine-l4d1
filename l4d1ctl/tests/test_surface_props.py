"""The surface property manifest has three separate questions, and this pins all
three: is the file in the content, would the engine's reader get anything out of
it, and does the chain of ``file`` entries end in surfaces that parse.

The engine half these tests mirror is ``PhysParseSurfaceData()`` in
``game/shared/physics_shared.cpp`` plus the two KeyValues functions it leans on:
``LoadFromFile`` (fails only on unopenable or empty) and ``LoadFromBuffer`` (never
fails; the first block's name becomes the root's name, later blocks become siblings
the manifest loop never visits).
"""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import contentaudit, surfaceprops, vpk

MANIFEST = "scripts/surfaceproperties_manifest.txt"
SURFACES = "scripts/surfaceproperties_left4dead.txt"

MANIFEST_TEXT = b"""surfaceproperties_manifest
{
\t"file"\t\t"scripts/surfaceproperties_left4dead.txt"
}
"""

SURFACES_TEXT = b""""default"
{
\t"base"\t\t"concrete"
\t"density"\t"2000"
}

"concrete"
{
\t"density"\t"2300"
}

"asphalt"
{
\t"density"\t"2100"
}
"""


class ParseTests(unittest.TestCase):
    def test_the_first_block_becomes_the_root_and_the_rest_are_siblings(self):
        document = surfaceprops.parse_document(MANIFEST_TEXT.decode())
        self.assertEqual(document.root_name, "surfaceproperties_manifest")
        self.assertEqual([node.name for node in document.children], ["file"])
        self.assertEqual(document.children[0].value, SURFACES)

    def test_a_second_block_is_parsed_but_never_visited(self):
        """LoadFromBuffer links it beside the root; the manifest loop asks for
        GetFirstSubKey(), which cannot reach it."""
        document = surfaceprops.parse_document(
            "first\n{\n\t\"file\"\t\"a.txt\"\n}\nsecond\n{\n\t\"file\"\t\"b.txt\"\n}\n")
        self.assertEqual(document.root_name, "first")
        self.assertEqual([n.value for n in document.children], ["a.txt"])
        self.assertEqual(document.sibling_names, ["second"])

    def test_a_name_without_a_brace_is_the_missing_brace_case(self):
        document = surfaceprops.parse_document("scripts/surfaceproperties.txt\n")
        self.assertTrue(document.missing_brace)
        self.assertEqual(document.children, [])

    def test_comments_and_conditionals_do_not_become_keys(self):
        document = surfaceprops.parse_document(
            '// the index\nroot\n{\n\t"file"\t"a.txt"  // one\n\t[$X360]\n}\n')
        self.assertEqual([node.value for node in document.children], ["a.txt"])

    def test_surface_files_are_a_sequence_of_blocks(self):
        document = surfaceprops.parse_document(SURFACES_TEXT.decode())
        self.assertEqual([block.name for block in document.named_blocks],
                         ["default", "concrete", "asphalt"])
        bases = [child.value for child in document.named_blocks[0].children
                 if child.name == "base"]
        self.assertEqual(bases, ["concrete"])


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "left4dead"
        self.game.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _pack(self, files):
        return vpk.build_vpk_v1(self.game / "pak01_dir.vpk", files)

    def test_a_manifest_and_its_file_inside_a_pack_is_ok(self):
        self._pack({MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT})
        report = surfaceprops.audit(self.root)
        self.assertTrue(report.found)
        self.assertTrue(report.loadable)
        self.assertEqual(report.entries, [SURFACES])
        self.assertEqual(report.files[0].surfaces, ["default", "concrete", "asphalt"])
        self.assertEqual(report.exit_code(), 0, report.problems)

    def test_a_pack_hit_is_usable_whatever_the_spelling(self):
        """VPK lookups compare with V_stricmp, so case cannot break them."""
        self._pack({"scripts/SurfaceProperties_Manifest.txt": MANIFEST_TEXT,
                    SURFACES: SURFACES_TEXT})
        report = surfaceprops.audit(self.root)
        self.assertTrue(report.chosen.in_pack)
        self.assertTrue(surfaceprops.usable(report.chosen))
        self.assertEqual(report.exit_code(), 0, report.problems)

    def test_a_loose_file_with_the_wrong_case_is_a_problem_for_the_engine(self):
        (self.game / "scripts").mkdir()
        (self.game / "scripts" / "SurfaceProperties_Manifest.txt").write_bytes(
            MANIFEST_TEXT)
        (self.game / "scripts" / "surfaceproperties_left4dead.txt").write_bytes(
            SURFACES_TEXT)
        report = surfaceprops.audit(self.root)
        self.assertTrue(report.found)
        self.assertFalse(report.chosen.in_pack)
        self.assertFalse(surfaceprops.usable(report.chosen))
        self.assertEqual(report.exit_code(), 1)
        self.assertTrue(any("spelled" in problem for problem in report.problems))

    def test_no_manifest_anywhere_is_exit_two_and_lists_what_is_there(self):
        self._pack({SURFACES: SURFACES_TEXT})
        report = surfaceprops.audit(self.root)
        self.assertFalse(report.found)
        self.assertEqual(report.exit_code(), 2)
        self.assertEqual([hit.path for hit in report.available], [SURFACES])
        self.assertEqual([hit.path for hit in report.fallbacks_present], [SURFACES])
        self.assertIn(SURFACES, surfaceprops.render(report))

    def test_a_zero_byte_manifest_is_the_empty_file_failure(self):
        """The engine's second and last way to fail: it opened with no content."""
        self._pack({MANIFEST: b"", SURFACES: SURFACES_TEXT})
        report = surfaceprops.audit(self.root)
        self.assertTrue(report.found)
        self.assertFalse(report.loadable)
        self.assertTrue(any("0 bytes" in problem for problem in report.problems))
        self.assertEqual(report.exit_code(), 1)

    def test_a_manifest_naming_a_file_that_is_not_there_says_so(self):
        self._pack({MANIFEST: MANIFEST_TEXT})
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.entries, [SURFACES])
        self.assertIsNone(report.files[0].hit)
        self.assertEqual(report.exit_code(), 1)

    def test_a_bare_list_is_a_shape_problem_not_a_load_failure(self):
        self._pack({MANIFEST: b"scripts/surfaceproperties_left4dead.txt\n",
                    SURFACES: SURFACES_TEXT})
        report = surfaceprops.audit(self.root)
        self.assertTrue(report.loadable)
        self.assertTrue(report.document.missing_brace)
        self.assertFalse(report.contributes)
        self.assertTrue(any("missing {" in problem for problem in report.problems))

    def test_a_subkey_that_is_not_file_is_reported_by_name(self):
        self._pack({MANIFEST: b'sp\n{\n\t"files"\t"scripts/x.txt"\n}\n'})
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.bogus, ["files"])
        self.assertTrue(any("files" in problem for problem in report.problems))

    def test_a_base_that_lands_on_nothing_is_reported(self):
        self._pack({MANIFEST: MANIFEST_TEXT,
                    SURFACES: b'"default"\n{\n\t"base"\t"unobtainium"\n}\n'})
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.files[0].missing_bases, ["unobtainium"])
        self.assertEqual(report.exit_code(), 1)

    def test_a_base_resolves_across_the_files_the_manifest_names(self):
        second = "scripts/surfaceproperties_ep2.txt"
        self._pack({MANIFEST: b'sp\n{\n\t"file"\t"%s"\n\t"file"\t"%s"\n}\n'
                              % (SURFACES.encode(), second.encode()),
                    SURFACES: SURFACES_TEXT,
                    second: b'"ice"\n{\n\t"base"\t"concrete"\n}\n'})
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.files[1].missing_bases, [])
        self.assertEqual(report.exit_code(), 0, report.problems)

    def test_the_report_says_what_each_pack_parsed_into(self):
        """A nil result has to be explainable: "no surface file here" is a different
        claim from "the reader did not get into the pack"."""
        self._pack({"materials/a.vmt": b"x",
                    "scripts/game_sounds_manifest.txt": b"// x\n"})
        report = surfaceprops.audit(self.root)
        self.assertEqual(len(report.packs), 1)
        self.assertEqual(report.packs[0].entries, 2)
        self.assertEqual(report.packs[0].under_scripts, 1)
        text = surfaceprops.render(report)
        self.assertIn("2 entries, 1 under scripts/", text)
        self.assertIn("has 1 scripts/ entries, so look at what they are called", text)

    def test_a_name_in_the_bytes_but_not_in_the_index_blames_the_reader(self):
        """Names live in the VPK tree, so if the tree lists none of them this says
        whether the pack has none or the reader misread it."""
        pack = self._pack({"materials/a.vmt": b"x"})
        pack.write_bytes(pack.read_bytes() + b"\0scripts/surfaceproperties.txt\0")
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.mentions[0][1], 1)
        text = surfaceprops.render(report)
        self.assertIn("the reader is wrong, not the content", text)

    def test_an_empty_index_is_named_rather_than_read_as_empty_content(self):
        (self.game / "pak01_dir.vpk").write_bytes(
            struct.pack("<III", 0x55AA1234, 1, 1) + b"\0")
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.packs[0].entries, 0)
        self.assertIn("could not read this pack", surfaceprops.render(report))

    def test_a_loose_manifest_outside_the_search_paths_is_named_and_flagged(self):
        """Finding a file is not the same as the engine being able to see it."""
        foreign = self.root / "garrysmod" / "scripts"
        foreign.mkdir(parents=True)
        (foreign / "surfaceproperties_manifest.txt").write_bytes(MANIFEST_TEXT)
        (foreign / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)
        report = surfaceprops.audit(self.root)
        self.assertTrue(report.found)
        self.assertEqual(report.chosen.located,
                         "garrysmod/scripts/surfaceproperties_manifest.txt")
        text = surfaceprops.render(report)
        self.assertIn("garrysmod/scripts/surfaceproperties_manifest.txt", text)
        self.assertIn("cannot see this file there", text)
        self.assertEqual(report.exit_code(), 1)
        self.assertTrue(any("invisible to the load" in problem
                            for problem in report.problems))
        self.assertIn("mkdir -p left4dead/scripts", text)

    def test_a_manifest_inside_the_game_directory_is_not_flagged(self):
        scripts = self.game / "scripts"
        scripts.mkdir()
        (scripts / "surfaceproperties_manifest.txt").write_bytes(MANIFEST_TEXT)
        (scripts / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)
        report = surfaceprops.audit(self.root)
        text = surfaceprops.render(report)
        self.assertIn("left4dead/scripts/surfaceproperties_manifest.txt", text)
        self.assertNotIn("cannot see this file there", text)
        self.assertEqual(report.exit_code(), 0, report.problems)

    def test_a_version_2_pack_is_read_with_all_its_scripts(self):
        """The truncation bug lived in the standalone readers; the package is pinned
        here so the two cannot drift apart again."""
        vpk.build_vpk_v2(self.game / "pak01_dir.vpk",
                         {MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT,
                          **{f"scripts/s{i}.txt": b"x" for i in range(20)}})
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.packs[0].entries, 22)
        self.assertEqual(report.packs[0].under_scripts, 22)
        self.assertEqual(report.exit_code(), 0, report.problems)

    def test_the_report_survives_a_pack_that_is_not_a_pack(self):
        (self.game / "pak01_dir.vpk").write_bytes(b"not a vpk at all")
        report = surfaceprops.audit(self.root)
        self.assertFalse(report.found)
        self.assertEqual(report.exit_code(), 2)


class StageTests(unittest.TestCase):
    """The fix the report recommends has to be one command, and it has to be safe."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = self.root / "left4dead"
        self.game.mkdir()
        self.foreign = self.root / "sourceengine" / "scripts"
        self.foreign.mkdir(parents=True)
        (self.foreign / "surfaceproperties_manifest.txt").write_bytes(MANIFEST_TEXT)
        (self.foreign / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)

    def test_staging_puts_the_chain_where_the_engine_looks(self):
        report = surfaceprops.audit(self.root)
        self.assertEqual(report.exit_code(), 1)
        surfaceprops.stage(report, self.game)
        self.assertEqual((self.game / "scripts" / "surfaceproperties_manifest.txt")
                         .read_bytes(), MANIFEST_TEXT)
        self.assertEqual((self.game / "scripts" / "surfaceproperties_left4dead.txt")
                         .read_bytes(), SURFACES_TEXT)
        self.assertEqual(surfaceprops.audit(self.root).exit_code(), 0)

    def test_existing_files_are_kept_unless_forced(self):
        (self.game / "scripts").mkdir()
        existing = self.game / "scripts" / "surfaceproperties_left4dead.txt"
        existing.write_bytes(b"mine")
        actions = surfaceprops.stage(surfaceprops.audit(self.root), self.game)
        self.assertIn("kept", [action for _source, _destination, action in actions])
        self.assertEqual(existing.read_bytes(), b"mine")

    def test_a_file_already_in_place_is_never_copied_onto_itself(self):
        """With the chain already staged, the source and the destination are the
        same file; rewriting it from its own bytes is a way to lose it."""
        (self.game / "scripts").mkdir()
        existing = self.game / "scripts" / "surfaceproperties_left4dead.txt"
        existing.write_bytes(b"mine")
        actions = surfaceprops.stage(surfaceprops.audit(self.root), self.game,
                                     force=True)
        self.assertEqual(existing.read_bytes(), b"mine")
        self.assertIn("kept", [action for _source, _destination, action in actions])

    def test_staging_with_nothing_found_is_an_error(self):
        empty = self.root / "nothing-here"
        empty.mkdir()
        report = surfaceprops.audit(empty)
        with self.assertRaises(ValueError):
            surfaceprops.stage(report, self.game)


class CommandTests(unittest.TestCase):
    def _run(self, *argv):
        return subprocess.run([sys.executable, "-m", "l4d1ctl", *argv],
                              capture_output=True, text=True)

    def test_json_output_is_parseable_and_carries_the_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT})
            result = self._run("surface", "--root", tmp, "--json")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["found"])
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["entries"], [SURFACES])
            self.assertEqual(payload["files"][0]["surfaces"],
                             ["default", "concrete", "asphalt"])

    def test_a_missing_manifest_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "left4dead").mkdir()
            result = self._run("surface", "--root", tmp)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("no manifest", result.stdout)

    def test_stage_copies_and_then_the_audit_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "left4dead").mkdir()
            foreign = root / "sourceengine" / "scripts"
            foreign.mkdir(parents=True)
            (foreign / "surfaceproperties_manifest.txt").write_bytes(MANIFEST_TEXT)
            (foreign / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)
            result = self._run("surface", "--root", tmp, "--stage",
                               str(root / "left4dead"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("copied", result.stdout)
            after = self._run("surface", "--root", tmp)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)

    def test_a_missing_directory_is_an_error_not_a_traceback(self):
        result = self._run("surface", "--root", "/nonexistent-srceng")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no such directory", result.stderr)


class StandaloneScriptTests(unittest.TestCase):
    """scripts/surface-manifest.py has to work with nothing but a python3."""

    SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "surface-manifest.py"

    def _run(self, target, *extra):
        return subprocess.run([sys.executable, str(self.SCRIPT), str(target), *extra],
                              capture_output=True, text=True)

    def _content(self, files):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        game = Path(handle.name) / "left4dead"
        game.mkdir()
        vpk.build_vpk_v1(game / "pak01_dir.vpk", files)
        return Path(handle.name)

    def test_it_finds_the_manifest_in_a_pack_and_counts_surfaces(self):
        root = self._content({MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT})
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pak01_dir.vpk, %d bytes" % len(MANIFEST_TEXT), result.stdout)
        self.assertIn("3 surfaces: default, concrete, asphalt", result.stdout)
        self.assertIn("resolves", result.stdout)

    def test_a_pack_with_no_manifest_exits_two_and_names_the_fallbacks(self):
        root = self._content({SURFACES: SURFACES_TEXT})
        result = self._run(root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no manifest in this content", result.stdout)
        self.assertIn(SURFACES, result.stdout)

    def test_a_loose_file_in_the_wrong_case_is_called_out(self):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        scripts = Path(handle.name) / "left4dead" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "SurfaceProperties_Manifest.txt").write_bytes(MANIFEST_TEXT)
        (scripts / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)
        result = self._run(Path(handle.name))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SurfaceProperties_Manifest.txt", result.stdout)
        self.assertIn("loose file keeps its own case", result.stdout)

    def test_a_zero_byte_entry_is_called_empty_not_malformed(self):
        root = self._content({MANIFEST: b"", SURFACES: SURFACES_TEXT})
        result = self._run(root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("0 bytes", result.stdout)

    def test_a_version_2_pack_is_read_in_full(self):
        root = self._content({"scripts/surfaceproperties_manifest.txt": MANIFEST_TEXT,
                              SURFACES: SURFACES_TEXT})
        vpk.build_vpk_v2(root / "left4dead" / "pak01_dir.vpk",
                         {MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT})
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("vpk v2", result.stdout)
        self.assertIn("3 surfaces", result.stdout)

    def test_a_loose_manifest_outside_the_search_path_is_flagged(self):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        foreign = Path(handle.name) / "garrysmod" / "scripts"
        foreign.mkdir(parents=True)
        (foreign / "surfaceproperties_manifest.txt").write_bytes(MANIFEST_TEXT)
        (foreign / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)
        result = self._run(Path(handle.name))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("garrysmod/scripts/surfaceproperties_manifest.txt",
                      result.stdout)
        self.assertIn("cannot see this file there", result.stdout)
        self.assertIn("mkdir -p left4dead/scripts", result.stdout)

    def test_staging_on_the_device_script_works_and_is_idempotent(self):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        root = Path(handle.name)
        game = root / "left4dead"
        game.mkdir()
        foreign = root / "sourceengine" / "scripts"
        foreign.mkdir(parents=True)
        (foreign / "surfaceproperties_manifest.txt").write_bytes(MANIFEST_TEXT)
        (foreign / "surfaceproperties_left4dead.txt").write_bytes(SURFACES_TEXT)

        first = self._run(root, "--stage", game)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("copied", first.stdout)
        self.assertIn("resolves", first.stdout)

        second = self._run(root, "--stage", game, "--force")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("kept", second.stdout)
        self.assertEqual((game / "scripts" / "surfaceproperties_left4dead.txt")
                         .read_bytes(), SURFACES_TEXT)

    def test_a_data_part_is_refused_with_the_name_of_the_index(self):
        """pak01_000.vpk is raw data; only the _dir.vpk has an index."""
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        game = Path(handle.name) / "left4dead"
        game.mkdir()
        vpk.build_vpk_v1(game / "pak01_dir.vpk",
                         {MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT},
                         split=True)
        part = game / "pak01_000.vpk"
        self.assertTrue(part.is_file())
        result = self._run(part)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("pak01_dir.vpk", result.stderr)
        self.assertIn("data part", result.stderr)

    def test_a_name_in_the_bytes_but_not_in_the_index_is_reported(self):
        root = self._content({"materials/a.vmt": b"x"})
        pack = root / "left4dead" / "pak01_dir.vpk"
        pack.write_bytes(pack.read_bytes() + b"\0scripts/surfaceproperties.txt\0")
        result = self._run(root)
        self.assertIn("the reader is wrong, not the content", result.stdout)

    def test_it_says_how_much_of_each_pack_it_read(self):
        root = self._content({"materials/a.vmt": b"x"})
        result = self._run(root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("vpk v1, 1 entries", result.stdout)
        self.assertIn("suspect the reader, not the content", result.stdout)

    def test_it_agrees_with_the_package_on_a_real_shaped_content_set(self):
        root = self._content({MANIFEST: MANIFEST_TEXT, SURFACES: SURFACES_TEXT})
        script = self._run(root)
        package = subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "surface", "--root", str(root)],
            capture_output=True, text=True)
        self.assertEqual(script.returncode, package.returncode)
        self.assertEqual(script.returncode, 0)
        self.assertIn("3 surfaces", script.stdout)
        self.assertIn("3 surfaces", package.stdout)


if __name__ == "__main__":
    unittest.main()
