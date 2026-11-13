import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import cli, sceneimage, vpk


def build_image(version=2, scenes=1, name=b"scenes/test.vcd", payload=b"\xde\xad\xbe\xef",
                scene_count=None):
    """A scene image laid out the way public/scenefilecache/SceneImageFile.h describes."""
    string_blob = name + b"\0"
    count = scenes if scene_count is None else scene_count
    entry_offset = 20 + 4            # header + one u32 string offset
    data_offset = entry_offset + 16 * count
    string_offset = data_offset + len(payload)
    parts = [
        struct.pack("<5i", sceneimage.SCENE_IMAGE_ID, version, count, 1, entry_offset),
        struct.pack("<I", string_offset),
    ]
    for _ in range(count):
        parts.append(struct.pack("<Iiii", 0x12345678, data_offset, len(payload), 0))
    parts.append(payload)
    parts.append(string_blob)
    return b"".join(parts)


class ParseTests(unittest.TestCase):
    def test_a_valid_image_is_accepted(self):
        report = sceneimage.parse(sceneimage.Source(label="memory", data=build_image()))
        self.assertTrue(report.ok, report.problems)
        self.assertEqual(report.id_text, "VSIF")
        self.assertEqual(report.version, 2)
        self.assertEqual(report.num_scenes, 1)
        self.assertEqual(report.strings, ["scenes/test.vcd"])
        self.assertEqual(report.entries[0]["data_length"], 4)

    def test_the_id_lands_in_the_file_as_fisv(self):
        """MAKEID('V','S','I','F') is a big-endian integer; on disk it reads FISV."""
        data = build_image()
        self.assertEqual(data[:4], b"FISV")
        self.assertEqual(data[:4], sceneimage.SCENE_IMAGE_FILE_BYTES)

    def test_a_byte_swapped_id_is_reported_not_hidden(self):
        data = bytearray(build_image())
        data[0:4] = b"VSIF"
        report = sceneimage.parse(sceneimage.Source(label="memory", data=bytes(data)))
        self.assertIn("byte order reversed", report.id_text)
        self.assertTrue(report.ok, report.problems)

    def test_another_version_is_named(self):
        report = sceneimage.parse(sceneimage.Source(label="memory", data=build_image(version=1)))
        self.assertFalse(report.ok)
        self.assertTrue(any("version 1" in p for p in report.problems), report.problems)
        self.assertIn("version 1", report.verdict)
        self.assertIn("fatal", report.verdict)

    def test_a_short_file_says_so(self):
        report = sceneimage.parse(sceneimage.Source(label="memory", data=b"FISV\x02\x00"))
        self.assertFalse(report.ok)
        self.assertIn("too short", report.verdict)

    def test_a_zero_header_points_at_the_read_not_the_format(self):
        report = sceneimage.parse(sceneimage.Source(label="memory", data=b"\0" * 64))
        self.assertFalse(report.ok)
        self.assertIn("zero", report.verdict)

    def test_a_truncated_file_is_caught_by_the_entry_table(self):
        data = bytearray(build_image())
        struct.pack_into("<i", data, 8, 40)   # claims 40 scenes, the file holds one
        report = sceneimage.parse(sceneimage.Source(label="memory", data=bytes(data)))
        self.assertFalse(report.ok)
        self.assertTrue(any("entry table ends at" in p for p in report.problems), report.problems)

    def test_entries_outside_the_file_are_flagged(self):
        data = bytearray(build_image())
        struct.pack_into("<i", data, 20 + 4 + 4, 1 << 20)   # nDataOffset beyond the end
        report = sceneimage.parse(sceneimage.Source(label="memory", data=bytes(data)))
        self.assertFalse(report.ok)
        self.assertTrue(any("outside" in p for p in report.problems), report.problems)

    def test_render_shows_the_head_and_the_verdict(self):
        text = sceneimage.render(sceneimage.parse(
            sceneimage.Source(label="pak01_dir.vpk -> scenes/scenes.image",
                              data=build_image())))
        self.assertIn("bytes:", text)
        self.assertIn("0000", text)
        self.assertIn("nNumScenes", text)
        self.assertIn("verdict:", text)


class LocateTests(unittest.TestCase):
    def test_reads_the_image_out_of_the_game_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image()})
            source = sceneimage.locate(game)
            self.assertEqual(source.archive, "pak01_dir.vpk")
            self.assertEqual(source.data, build_image())
            self.assertEqual(source.entry_size, len(build_image()))

    def test_a_loose_file_wins_over_the_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            (game / "scenes").mkdir(parents=True)
            (game / sceneimage.SCENE_IMAGE_PATH).write_bytes(build_image(version=1))
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image()})
            source = sceneimage.locate(game)
            self.assertIsNone(source.archive)
            self.assertEqual(sceneimage.parse(source).version, 1)

    def test_pak01_is_preferred_over_other_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "aaa_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image(version=1)})
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image()})
            self.assertEqual(sceneimage.locate(game).archive, "pak01_dir.vpk")

    def test_a_pack_that_does_not_hold_the_image_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "pak01_dir.vpk", {"scripts/other.txt": b"x"})
            with self.assertRaises(sceneimage.SceneImageError):
                sceneimage.locate(game)


class CommandTests(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_json_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image()})
            code, out, _ = self._run(["scene", str(game), "--json"])
            payload = json.loads(out)
            self.assertEqual(payload["id"], "VSIF")
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["archive"], "pak01_dir.vpk")
            self.assertTrue(payload["ok"])
            self.assertEqual(code, 0)

    def test_a_rejected_image_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image(version=1)})
            code, out, _ = self._run(["scene", str(game)])
            self.assertEqual(code, 1)
            self.assertIn("version 1", out)

    def test_write_saves_the_bytes_for_offline_looking(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "left4dead"
            game.mkdir()
            vpk.build_vpk_v1(game / "pak01_dir.vpk",
                             {sceneimage.SCENE_IMAGE_PATH: build_image()})
            target = Path(tmp) / "copy.image"
            self._run(["scene", str(game), "--write", str(target)])
            self.assertEqual(target.read_bytes(), build_image())

    def test_missing_target_is_a_clean_error(self):
        code, _, err = self._run(["scene", "/nonexistent/game"])
        self.assertEqual(code, 2)
        self.assertIn("no such file or directory", err)


if __name__ == "__main__":
    unittest.main()
