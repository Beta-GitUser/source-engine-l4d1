import tempfile
import unittest
from pathlib import Path

from l4d1ctl import vpk

FILES = {
    "materials/models/survivor/zoey.vmt": b"VertexLitGeneric {}",
    "materials/models/survivor/zoey.vtf": b"\x00" * 64,
    "models/survivor/zoey.mdl": b"mdl" * 10,
    "scripts/survivors.txt": b"Zoey\nLouis\n",
    "root.txt": b"top level",
}


class BuildAndReadTests(unittest.TestCase):
    def test_roundtrip_dir_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mymod_dir.vpk"
            vpk.build_vpk_v1(path, FILES)
            archive = vpk.VPK(path)
            self.assertEqual(archive.info.version, 1)
            self.assertEqual(len(archive), len(FILES))
            for name, content in FILES.items():
                self.assertEqual(archive.read(name), content, name)
            self.assertEqual(archive.verify(), [])
            self.assertEqual(archive.info.parts, [])

    def test_roundtrip_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mymod_dir.vpk"
            vpk.build_vpk_v1(path, FILES, split=True)
            archive = vpk.VPK(path)
            self.assertEqual([p.name for p in archive.info.parts], ["mymod_000.vpk"])
            for name, content in FILES.items():
                self.assertEqual(archive.read(name), content, name)
            self.assertEqual(archive.verify(), [])

    def test_missing_part_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mymod_dir.vpk"
            vpk.build_vpk_v1(path, FILES, split=True)
            (Path(tmp) / "mymod_000.vpk").unlink()
            archive = vpk.VPK(path)
            self.assertTrue(any("missing part" in p for p in archive.verify()))
            with self.assertRaises(vpk.VPKError):
                archive.read("models/survivor/zoey.mdl")

    def test_crc(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m_dir.vpk"
            vpk.build_vpk_v1(path, {"a.txt": b"hello"})
            archive = vpk.VPK(path)
            self.assertTrue(archive.crc_matches("a.txt"))
            self.assertFalse(archive.crc_matches("missing.txt"))

    def test_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m_dir.vpk"
            vpk.build_vpk_v1(path, FILES)
            out = Path(tmp) / "out"
            written = vpk.VPK(path).extract_all(out)
            self.assertEqual(written, len(FILES))
            self.assertEqual((out / "models/survivor/zoey.mdl").read_bytes(),
                             FILES["models/survivor/zoey.mdl"])
            again = vpk.VPK(path).extract_all(out)
            self.assertEqual(again, 0)

    def test_glob_and_find(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m_dir.vpk"
            vpk.build_vpk_v1(path, FILES)
            archive = vpk.VPK(path)
            self.assertEqual(len(archive.glob("vmt")), 1)
            self.assertEqual(len(archive.glob(".vtf")), 1)
            self.assertIsNotNone(archive.find("ROOT.TXT"))  # case-insensitive
            self.assertIsNone(archive.find("nope.txt"))


class ErrorTests(unittest.TestCase):
    def test_bad_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_dir.vpk"
            path.write_bytes(b"\x00" * 64)
            with self.assertRaises(vpk.VPKError):
                vpk.VPK(path)

    def test_too_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny_dir.vpk"
            path.write_bytes(b"\x34\x12\xaa\x55")
            with self.assertRaises(vpk.VPKError):
                vpk.VPK(path)

    def test_unsupported_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            import struct

            path = Path(tmp) / "v9_dir.vpk"
            path.write_bytes(struct.pack("<III", vpk.VPK_SIGNATURE, 9, 0) + b"\x00" * 16)
            with self.assertRaises(vpk.VPKError):
                vpk.VPK(path)

    def test_missing_file(self):
        with self.assertRaises(vpk.VPKError):
            vpk.VPK(Path("/nonexistent/x_dir.vpk"))

    def test_no_extension_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                vpk.build_vpk_v1(Path(tmp) / "x_dir.vpk", {"noext": b"x"})

    def test_empty_vpk_is_suspicious(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty_dir.vpk"
            vpk.build_vpk_v1(path, {})
            archive = vpk.VPK(path)
            self.assertEqual(len(archive), 0)
            self.assertTrue(any("no files" in p for p in archive.verify()))


class HelperTests(unittest.TestCase):
    def test_find_dir_vpk(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            vpk.build_vpk_v1(folder / "one_dir.vpk", {"a.txt": b"x"})
            self.assertEqual(vpk.find_dir_vpk(folder).name, "one_dir.vpk")
            self.assertEqual(vpk.find_dir_vpk(folder / "one_dir.vpk").name, "one_dir.vpk")

    def test_summarise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m_dir.vpk"
            vpk.build_vpk_v1(path, {"a.txt": b"abc"})
            summary = vpk.summarise(path)
            self.assertEqual(summary["entries"], 1)
            self.assertEqual(summary["payload_bytes"], 3)
            self.assertEqual(summary["problems"], [])


if __name__ == "__main__":
    unittest.main()
