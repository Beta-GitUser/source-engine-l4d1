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


class StandaloneTests(unittest.TestCase):
    """`mine.py` must work as a single downloaded file: no package, no clone.

    The phone workflow depends on it - Termux, one `curl`, run it. So the test
    copies the file somewhere unrelated and runs it there with the repository
    off sys.path.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.script = self.tmp / "mine.py"
        self.script.write_bytes((ROOT / "l4d1ctl" / "mine.py").read_bytes())
        self.binary = self.tmp / "client.dll"
        self.binary.write_bytes(build_pe(STRINGS))

    def tearDown(self):
        self._tmp.cleanup()

    def run_standalone(self, *args):
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            capture_output=True, text=True, cwd=str(self.tmp),
        )

    def test_no_package_imports(self):
        source = (ROOT / "l4d1ctl" / "mine.py").read_text()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and not stripped.startswith("from __future__"):
                module = stripped.split()[1].split(".")[0]
                self.assertIn(module, {
                    "argparse", "json", "re", "struct", "sys", "dataclasses", "pathlib", "typing",
                }, f"{stripped!r} is not standard library - the single-file workflow breaks")

    def test_reports_without_the_package(self):
        result = self.run_standalone("client.dll")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VClient017", result.stdout)

    def test_bucket_without_the_package(self):
        result = self.run_standalone("client.dll", "--bucket", "netprops")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["m_iHealth", "m_vecOrigin"])

    def test_json_without_the_package(self):
        result = self.run_standalone("client.dll", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["bits"], 32)


def stdout_payload(text):
    """`mine --json` may be preceded by nothing, but tolerate stray output."""
    start = text.index("{") if "{" in text else text.index("[")
    return text[start:]


if __name__ == "__main__":
    unittest.main()


class RealSampleTests(unittest.TestCase):
    """Regressions from a real run against retail L4D1 client.dll.

    l4d1ctl/tests/fixtures/mine_l4d1_sample.txt holds strings copied out of that
    report, each tagged with the bucket it belongs in, or "none" for strings
    that should not survive classification. The junk in that first run was not
    random - image formats ending in digits, ALL_CAPS enum tokens, capitalised
    display names, sentences ending in ".txt" - so each class gets a case.
    """

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mine_l4d1_sample.txt"

    @classmethod
    def setUpClass(cls):
        cls.rows = []
        for line in cls.FIXTURE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            bucket, name = line.split("\t")
            cls.rows.append((bucket, name))
        cls.payload = "\n".join(name for _, name in cls.rows).encode() + b"\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        path = self.tmp / "client.dll"
        path.write_bytes(build_pe([line.encode() for line in
                                   self.payload.decode().splitlines() if line]))
        self.inventory = mine.mine(path)

    def tearDown(self):
        self._tmp.cleanup()

    def bucket_of(self, name):
        for bucket, items in self.inventory.buckets().items():
            if name in items:
                return bucket
        return "none"

    def test_every_row_lands_in_its_bucket(self):
        wrong = [(bucket, name, self.bucket_of(name))
                 for bucket, name in self.rows if self.bucket_of(name) != bucket]
        self.assertEqual(wrong, [], "bucket\tname\tlanded_in")

    def test_image_formats_are_not_interfaces(self):
        for name in ("ABGR8888", "ARGB8888", "BGR565", "IMAGE_FORMAT_UVLX8888"):
            self.assertNotIn(name, self.inventory.interfaces)

    def test_animation_activities_are_not_interfaces(self):
        self.assertNotIn("ACT_PRIMARYATTACK_XM1014", self.inventory.interfaces)
        self.assertNotIn("EjectBrass_556", self.inventory.interfaces)

    def test_sentences_are_not_assets(self):
        self.assertFalse(any(" " in asset for asset in self.inventory.assets))

    def test_system_libraries_are_not_assets(self):
        for name in ("ADVAPI32.dll", "KERNEL32.DLL"):
            self.assertNotIn(name, self.inventory.assets)

    def test_capitalised_display_names_are_not_entities(self):
        for name in ("Boomer", "BOOMER", "Tank"):
            self.assertNotIn(name, self.inventory.entities)

    def test_sdk_alignment_is_computed(self):
        alignment = self.inventory.alignment
        self.assertIsNotNone(alignment)
        self.assertIn("VClient017", alignment.known)
        # Retail L4D1 wants 006; this SDK's header says 008.
        module_name, sdk_versions = alignment.mismatched["MatSystemSurface"]
        self.assertEqual(module_name, "MatSystemSurface006")
        self.assertNotIn(6, sdk_versions)
        self.assertIn("IMATCHEXT_L4D_INTERFACE_001", alignment.unknown)

    def test_report_surfaces_mismatches_and_unknowns(self):
        text = mine.report(self.inventory, top=5)
        self.assertIn("version mismatches", text)
        self.assertIn("MatSystemSurface006", text)
        self.assertIn("L4D-only", text)

    def test_json_carries_the_alignment(self):
        payload = json.loads(mine.dump_json(self.inventory))
        mismatch = payload["sdk_alignment"]["mismatched"]["MatSystemSurface"]
        self.assertEqual(mismatch["module"], "MatSystemSurface006")
        self.assertNotIn(6, mismatch["sdk_versions"])


class SdkVersionSetTests(unittest.TestCase):
    """Several declared versions per family is normal, not an inconsistency."""

    def test_any_declared_version_counts_as_known(self):
        for name in ("ServerGameDLL008", "ServerGameDLL009", "ServerGameDLL010"):
            alignment = mine.align_with_sdk([name])
            self.assertEqual(alignment.known, [name], name)
            self.assertEqual(alignment.mismatched, {}, name)

    def test_a_version_outside_the_set_is_a_mismatch(self):
        alignment = mine.align_with_sdk(["ServerGameDLL011"])
        module_name, versions = alignment.mismatched["ServerGameDLL"]
        self.assertEqual(module_name, "ServerGameDLL011")
        self.assertNotIn(11, versions)
        self.assertIn(10, versions)

    def test_server_interfaces_are_in_the_generated_list(self):
        """They are named INTERFACEVERSION_*, not *_INTERFACE_VERSION, and the
        generator missing them would hide exactly the server-side work."""
        for family in ("ServerGameDLL", "ServerGameClients", "VEngineServer"):
            self.assertTrue([name for name in mine.SDK_INTERFACES if name.startswith(family)],
                            f"{family} missing from the generated list")

    def test_the_report_lists_every_declared_version(self):
        inventory = mine.Inventory(path="x.dll", machine="i386", bits=32, size=1)
        inventory.interfaces = ["ServerGameDLL011"]
        inventory.alignment = mine.align_with_sdk(inventory.interfaces)
        text = mine.report(inventory, top=10)
        self.assertIn("ServerGameDLL011", text)
        self.assertIn("ServerGameDLL008", text)


class SdkInterfaceTests(unittest.TestCase):
    def test_generated_list_is_not_empty(self):
        self.assertGreater(len(mine.SDK_INTERFACES), 100)

    def test_generated_list_is_current(self):
        """mine.py must match the headers; scripts/gen-sdk-interfaces.sh owns it."""
        result = subprocess.run(["sh", str(ROOT / "scripts" / "gen-sdk-interfaces.sh"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr + "\n(run scripts/gen-sdk-interfaces.sh)")

    def test_version_split(self):
        self.assertEqual(mine.interface_family("MatSystemSurface006"), ("MatSystemSurface", 6))
        self.assertEqual(mine.interface_family("GMATCHFRAMEWORK_001"), ("GMATCHFRAMEWORK", 1))
        self.assertEqual(mine.interface_family("VClient017"), ("VClient", 17))
        self.assertEqual(mine.interface_family("NotAnInterface"), ("NotAnInterface", None))


class RetailInterfaceFixtureTests(unittest.TestCase):
    """The retail interface lists, and the page built from them.

    These fixtures came from real runs against depot 503/502 and are the only
    real-world data in the test suite. They catch the things synthetic strings
    cannot: whether the classifier rejects what a real Valve build contains, and
    whether the conclusions we wrote down still match the data.
    """

    FIXTURES = Path(__file__).resolve().parent / "fixtures"
    DOC = ROOT / "docs" / "L4D1" / "INTERFACES.md"

    def load(self, name):
        raw = [line.strip() for line in
               (self.FIXTURES / name).read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.strip().startswith("#")]
        real = [line for line in raw if mine._INTERFACE_RE.match(line)]
        return raw, real

    def test_client_fixture_rejects_image_formats(self):
        _, real = self.load("retail_client_interfaces.txt")
        for name in ("ABGR8888", "RGB565", "IMAGE_FORMAT_UVLX8888", "UVWQ8888"):
            self.assertNotIn(name, real)

    def test_server_fixture_rejects_activity_names(self):
        _, real = self.load("retail_server_interfaces.txt")
        self.assertNotIn("ACT_PRIMARYATTACK_XM1014", real)
        self.assertNotIn("VP4001", real)

    def test_client_versions_of_interest(self):
        _, real = self.load("retail_client_interfaces.txt")
        alignment = mine.align_with_sdk(real)
        self.assertEqual(alignment.mismatched["VClient"][0], "VClient016")
        self.assertEqual(alignment.mismatched["VFileSystem"][0], "VFileSystem018")
        self.assertIn("IMATCHEXT_L4D_INTERFACE_001", alignment.unknown)
        self.assertEqual(len(real), 77, "the fixture changed - update INTERFACES.md")

    def test_server_gets_the_older_game_dll_interface(self):
        """L4D1's server module wants ServerGameDLL005; this SDK has 008-010."""
        _, real = self.load("retail_server_interfaces.txt")
        alignment = mine.align_with_sdk(real)
        module_name, versions = alignment.mismatched["ServerGameDLL"]
        self.assertEqual(module_name, "ServerGameDLL005")
        self.assertNotIn(5, versions)
        self.assertIn("ServerGameClients003", alignment.known)

    def test_engine_services_shared_by_both_modules(self):
        _, client = self.load("retail_client_interfaces.txt")
        _, server = self.load("retail_server_interfaces.txt")
        shared = set(client) & set(server)
        # The framework both halves sit on; if these stopped overlapping, the
        # "HL2 game code is a usable base" argument would need revisiting.
        for name in ("MDLCache004", "VPhysics031", "VGUI_Surface030", "VDataCache003"):
            self.assertIn(name, shared)

    def test_doc_still_describes_the_data(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "interface-report.py"),
             "--check-doc", str(self.DOC),
             str(self.FIXTURES / "retail_client_interfaces.txt"),
             str(self.FIXTURES / "retail_server_interfaces.txt")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
