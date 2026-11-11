"""Tests for l4d1ctl.mine.

No retail binary is available (and never will be in CI), so the tests build
synthetic PE images: a COFF header, an optional header, one section, an export
directory exporting CreateInterface, and a payload of strings. That is enough to
pin down the parts that break silently - header parsing, RVA translation,
export walking, and the classifiers.
"""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import mine

ROOT = Path(__file__).resolve().parents[2]

STRINGS = [
    b"CreateInterface",
    b"VClient017",
    b"VEngineClient014",
    b"VGameServer023",
    b"m_iHealth",
    b"m_vecOrigin",
    b"DT_BasePlayer",
    b"DT_WeaponSMG",
    b"cl_showfps",
    b"sv_cheats",
    b"z_common_limit",
    b"director_panic_wave_size",
    b"weapon_smg",
    b"prop_physics",
    b"infected",
    b"tank",
    b"models/survivors/survivor_namvet.mdl",
    b"materials/models/survivors/namvet.vmt",
    b"sound/music/l4d/tank.wav",
    b"not a name at all",
    b"x",
]


def _align(value, boundary=0x200):
    return (value + boundary - 1) // boundary * boundary


def build_pe(payload_strings, *, bits=32, export_names=("CreateInterface",),
             machine=0x014C):
    """Assemble a minimal but valid PE image with one section.

    ``payload_strings`` are the fake binary's strings; ``export_names`` also go
    into the export directory so the export walker has something to find.
    """
    payload = [value.encode() if isinstance(value, str) else value
               for value in payload_strings]
    exports = [value.encode() if isinstance(value, str) else value
               for value in export_names]

    pool = payload + [text for text in exports if text not in payload]

    blob = b""
    string_rvas = []
    for text in pool:
        string_rvas.append((text, len(blob)))
        blob += text + b"\0"
    while len(blob) % 16:
        blob += b"\0"

    section_rva = 0x1000
    export_size = 40 + 4 * len(exports)
    export_offset = len(blob)
    names_table_offset = export_offset + 40
    blob += b"\0" * export_size

    strings_offset = _align(len(blob), 16)
    blob += b"\0" * (strings_offset - len(blob))
    strings_base = strings_offset
    for text, _ in string_rvas:
        blob += text + b"\0"
    blob += b"\0" * 64  # slack so the UTF-16 scan has room to finish a run

    section = bytearray(blob)

    # Name pointers point into the payload copy of the string.
    by_text = {text: rva for text, rva in string_rvas}
    name_table = b"".join(struct.pack("<I", section_rva + strings_base + by_text[text])
                          for text in exports)

    tail = _align(len(section), 16)
    section += b"\0" * (tail - len(section))
    functions_offset = len(section)
    section += b"\0" * (4 * len(exports) or 4)
    ordinals_offset = len(section)
    section += b"".join(struct.pack("<H", index) for index in range(len(exports)))

    section[names_table_offset:names_table_offset + len(name_table)] = name_table
    directory = struct.pack(
        "<IIHHIIIIIII",
        0, 0, 0, 0,             # characteristics, timestamp, versions
        0,                      # name rva
        1,                      # ordinal base
        len(exports),           # number of functions
        len(exports),           # number of names
        section_rva + functions_offset,
        section_rva + names_table_offset,
        section_rva + ordinals_offset,
    )
    section[export_offset:export_offset + 40] = directory

    raw_size = _align(len(section))
    section += b"\0" * (raw_size - len(section))

    optional_size = 224 if bits == 32 else 240
    optional = bytearray(optional_size)
    struct.pack_into("<H", optional, 0, 0x10B if bits == 32 else 0x20B)
    directories = 96 if bits == 32 else 112
    struct.pack_into("<II", optional, directories, section_rva + export_offset, export_size)

    headers = b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x40)
    headers += b"PE\0\0" + struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, optional_size, 0x2102)
    headers += bytes(optional)
    headers += struct.pack("<8sIIIIIIHHI", b".rdata\0\0", len(section), section_rva,
                           raw_size, 0x200, 0, 0, 0, 0, 0x40000040)
    body = headers + b"\0" * (0x200 - len(headers))
    return body + bytes(section)


class PortableExecutableTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, blob, name="client.dll"):
        path = self.tmp / name
        path.write_bytes(blob)
        return path

    def test_reads_headers(self):
        path = self.write(build_pe(STRINGS, bits=32))
        pe = mine.PortableExecutable.read(path)
        self.assertEqual(pe.bits, 32)
        self.assertEqual(pe.machine_name, "i386")
        self.assertEqual([section.name for section in pe.sections], [".rdata"])

    def test_reads_64_bit_images(self):
        pe = mine.PortableExecutable.read(self.write(build_pe(STRINGS, bits=64, machine=0x8664)))
        self.assertEqual(pe.bits, 64)
        self.assertEqual(pe.machine_name, "x86-64")

    def test_reads_exports(self):
        path = self.write(build_pe(STRINGS, export_names=("CreateInterface", "s_pInterface")))
        pe = mine.PortableExecutable.read(path)
        self.assertEqual(sorted(pe.exports()), ["CreateInterface", "s_pInterface"])

    def test_rejects_non_pe(self):
        path = self.write(b"this is not a binary\n")
        with self.assertRaises(mine.NotAPortableExecutable):
            mine.PortableExecutable.read(path)

    def test_rejects_truncated_header(self):
        path = self.write(b"MZ" + b"\0" * 10)
        with self.assertRaises(mine.NotAPortableExecutable):
            mine.PortableExecutable.read(path)

    def test_strings_finds_ascii(self):
        pe = mine.PortableExecutable.read(self.write(build_pe(STRINGS)))
        found = set(pe.strings(minimum=4))
        self.assertIn("m_iHealth", found)
        self.assertIn("VClient017", found)
        self.assertNotIn("x", found)  # shorter than the minimum


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        path = self.tmp / "client.dll"
        path.write_bytes(build_pe(STRINGS))
        self.inventory = mine.mine(path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_interfaces(self):
        self.assertIn("VClient017", self.inventory.interfaces)
        self.assertIn("VEngineClient014", self.inventory.interfaces)
        self.assertIn("VGameServer023", self.inventory.interfaces)

    def test_netprops(self):
        self.assertEqual(self.inventory.netprops, ["m_iHealth", "m_vecOrigin"])

    def test_datatables(self):
        self.assertEqual(self.inventory.datatables, ["DT_BasePlayer", "DT_WeaponSMG"])

    def test_convars_by_family(self):
        for name in ("cl_showfps", "sv_cheats", "z_common_limit",
                     "director_panic_wave_size"):
            self.assertIn(name, self.inventory.convars)

    def test_entities(self):
        for name in ("weapon_smg", "prop_physics", "infected", "tank"):
            self.assertIn(name, self.inventory.entities)

    def test_assets_keep_their_paths(self):
        self.assertIn("models/survivors/survivor_namvet.mdl", self.inventory.assets)
        self.assertIn("sound/music/l4d/tank.wav", self.inventory.assets)

    def test_exports_bucket(self):
        self.assertIn("CreateInterface", self.inventory.exports)

    def test_buckets_do_not_overlap(self):
        """A name belongs to exactly one bucket, or the report lies about counts."""
        seen = {}
        for name, items in self.inventory.buckets().items():
            for item in items:
                self.assertNotIn(item, seen, f"{item!r} is in both {seen.get(item)} and {name}")
                seen[item] = name

    def test_sentence_is_not_an_identifier(self):
        everything = {item for items in self.inventory.buckets().values() for item in items}
        self.assertNotIn("not a name at all", everything)


class WarningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_warns_about_64_bit_images(self):
        path = self.tmp / "x64.dll"
        path.write_bytes(build_pe(STRINGS, bits=64, machine=0x8664))
        inventory = mine.mine(path)
        self.assertTrue(any("32-bit" in warning for warning in inventory.warnings))

    def test_warns_about_a_packed_looking_image(self):
        path = self.tmp / "packed.dll"
        path.write_bytes(build_pe([b"ab", b"cd"], export_names=()))
        inventory = mine.mine(path, minimum=4)
        self.assertTrue(any("packed" in warning for warning in inventory.warnings))


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_compare_reports_one_sided_names(self):
        client = self.tmp / "client.dll"
        server = self.tmp / "server.dll"
        client.write_bytes(build_pe([b"VClient017", b"m_iHealth", b"cl_showfps"]))
        server.write_bytes(build_pe([b"VGameServer023", b"m_iHealth", b"sv_cheats"]))
        comparison = mine.compare(mine.mine(client), mine.mine(server))
        self.assertIn("VClient017", comparison.only_left["interfaces"])
        self.assertIn("VGameServer023", comparison.only_right["interfaces"])
        self.assertNotIn("m_iHealth", comparison.only_left["netprops"])
        text = mine.report_comparison(comparison)
        self.assertIn("client.dll", text)
        self.assertIn("VGameServer023", text)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.binary = self.tmp / "client.dll"
        self.binary.write_bytes(build_pe(STRINGS))

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "l4d1ctl", *args],
                              capture_output=True, text=True, cwd=str(ROOT))

    def test_report_lists_the_buckets(self):
        result = self.run_cli("mine", str(self.binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("interfaces (", result.stdout)
        self.assertIn("VClient017", result.stdout)
        self.assertIn("netprops (2)", result.stdout)

    def test_json_is_machine_readable(self):
        result = self.run_cli("mine", str(self.binary), "--json")
        payload = json.loads(stdout_payload(result.stdout))
        self.assertEqual(payload["bits"], 32)
        self.assertIn("VClient017", payload["interfaces"])
        self.assertIn("counts", payload)

    def test_bucket_prints_bare_names(self):
        result = self.run_cli("mine", str(self.binary), "--bucket", "netprops")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["m_iHealth", "m_vecOrigin"])

    def test_unknown_bucket_is_an_error(self):
        result = self.run_cli("mine", str(self.binary), "--bucket", "nonsense")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no bucket", result.stderr)

    def test_missing_file_is_an_error(self):
        result = self.run_cli("mine", str(self.tmp / "absent.dll"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    def test_not_a_pe_is_an_error(self):
        junk = self.tmp / "junk.dll"
        junk.write_bytes(b"nope\n")
        result = self.run_cli("mine", str(junk))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no MZ header", result.stderr)


def stdout_payload(text):
    """`mine --json` may be preceded by nothing, but tolerate stray output."""
    start = text.index("{") if "{" in text else text.index("[")
    return text[start:]


if __name__ == "__main__":
    unittest.main()
