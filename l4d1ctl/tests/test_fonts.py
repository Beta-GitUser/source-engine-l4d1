"""The font chain's first stage: what the scheme actually asks for.

Whether a missing label is a read problem, a format problem or a scheme problem
decides which of three very different fixes is worth a build, and all three look
identical from the engine log. These tests pin the reading of the scheme, including
the two ways a scheme can point at a custom font.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import fonts

SCHEME_VFONT = """Scheme
{
\tCustomFontFiles
\t{
\t\t"resource/HALFLIFE2.vfont"
\t\t"resource/hl2crosshairs.vfont"
\t}

\tFonts
\t{
\t\tDefault
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"name"\t\t"HALFLIFE2"
\t\t\t\t"tall"\t\t"16"
\t\t\t\t"weight"\t"500"
\t\t\t\t"antialias"\t"1"
\t\t\t}
\t\t}
\t\tDefaultBold
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"name"\t\t"HALFLIFE2"
\t\t\t\t"tall"\t\t"16"
\t\t\t\t"weight"\t"700"
\t\t\t}
\t\t}
\t}
}
"""

SCHEME_TTF = """Scheme
{
\tCustomFontFiles
\t{
\t\t"resource/DejaVuSans.ttf"
\t}

\tFonts
\t{
\t\tDefault
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"custom"\t"resource/DejaVuSans.ttf"
\t\t\t\t"tall"\t"18"
\t\t\t}
\t\t}
\t}
}
"""


# L4D1's own shape: numbered indices in CustomFontFiles, system font names in Fonts.
SCHEME_L4D1 = """Scheme
{
\tCustomFontFiles
\t{
\t\t"1"\t\t"resource/HALFLIFE2.vfont"
\t\t"2"\t\t"resource/HL2EP2.vfont"
\t\t"3"\t\t"resource/TG.vfont"
\t\t"4"\t\t"resource/TGB.vfont"
\t\t"5"\t\t"resource/marlett.ttf"
\t}

\tFonts
\t{
\t\tDebugFixed
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"name"\t\t"Courier New"
\t\t\t\t"tall"\t\t"10"
\t\t\t\t"weight"\t"500"
\t\t\t}
\t\t}
\t\tDefault
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"name"\t\t"Tahoma"
\t\t\t\t"tall"\t\t"16"
\t\t\t\t"weight"\t"500"
\t\t\t}
\t\t}
\t\tDefaultBold
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"name"\t\t"Tahoma"
\t\t\t\t"tall"\t\t"16"
\t\t\t\t"weight"\t"1000"
\t\t\t}
\t\t}
\t}
}
"""


class FontSchemeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fonts-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        self.game = self.dir / "left4dead"
        (self.game / "resource").mkdir(parents=True)

    def write_scheme(self, text, extras=()):
        (self.game / "resource" / "SourceScheme.res").write_text(text)
        for name in extras:
            target = self.game / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")

    def test_a_vfont_scheme_is_reported_as_unloadable_here(self):
        self.write_scheme(SCHEME_VFONT, ["resource/HALFLIFE2.vfont",
                                         "resource/hl2crosshairs.vfont"])
        report = fonts.read(self.game)
        self.assertTrue(report.parsed, report.error)
        self.assertEqual(len(report.fonts), 2)
        self.assertTrue(report.vfont_only)
        self.assertEqual(report.fonts[0].via_custom_file, "resource/HALFLIFE2.vfont")
        self.assertTrue(report.fonts[0].is_vfont)

    def test_the_two_entries_of_a_custom_font_files_block_are_both_read(self):
        """KeyValues pairs consecutive bare tokens, so they arrive as key and value."""
        self.write_scheme(SCHEME_VFONT)
        report = fonts.read(self.game)
        self.assertEqual(report.custom_font_files,
                         ["resource/HALFLIFE2.vfont", "resource/hl2crosshairs.vfont"])

    def test_a_ttf_scheme_is_not_vfont_only(self):
        self.write_scheme(SCHEME_TTF, ["resource/DejaVuSans.ttf"])
        report = fonts.read(self.game)
        self.assertFalse(report.vfont_only)
        self.assertEqual(report.fonts[0].custom, "resource/DejaVuSans.ttf")
        self.assertEqual(report.fonts[0].tall, "18")

    def test_a_missing_font_file_is_named(self):
        self.write_scheme(SCHEME_VFONT)
        report = fonts.read(self.game)
        self.assertIn("resource/HALFLIFE2.vfont", report.missing_files)

    def test_a_system_font_is_told_apart_from_a_custom_one(self):
        self.write_scheme(SCHEME_TTF.replace('"custom"\t"resource/DejaVuSans.ttf"',
                                             '"name"\t"Verdana"'),
                          ["resource/DejaVuSans.ttf"])
        report = fonts.read(self.game)
        self.assertEqual(report.fonts[0].source, "Verdana")
        self.assertFalse(report.fonts[0].is_vfont)

    def test_numbered_custom_font_indices_are_not_taken_for_paths(self):
        """L4D1 numbers its CustomFontFiles entries: "1" "resource/HALFLIFE2.vfont"."""
        self.write_scheme(SCHEME_L4D1, ["resource/HALFLIFE2.vfont",
                                        "resource/HL2EP2.vfont",
                                        "resource/TG.vfont",
                                        "resource/TGB.vfont",
                                        "resource/marlett.ttf"])
        report = fonts.read(self.game)
        self.assertEqual(report.custom_font_files,
                         ["resource/HALFLIFE2.vfont", "resource/HL2EP2.vfont",
                          "resource/TG.vfont", "resource/TGB.vfont",
                          "resource/marlett.ttf"])
        self.assertEqual(report.missing_files, [])

    def test_a_scheme_of_system_fonts_is_not_called_vfont_only(self):
        """L4D1's fonts are Tahoma and Courier New, not the .vfont files it ships."""
        self.write_scheme(SCHEME_L4D1, ["resource/HALFLIFE2.vfont"])
        report = fonts.read(self.game)
        self.assertFalse(report.vfont_only)
        self.assertEqual(len(report.fonts), 3)
        self.assertEqual(report.fonts[1].source, "Tahoma")
        self.assertEqual(report.fonts[1].tall, "16")

    def test_a_font_attribute_directly_under_fonts_is_flagged_not_counted(self):
        """A stray "name" under Fonts is a brace problem in the scheme, not a font."""
        broken = SCHEME_L4D1.replace("\t}\n}\n", "\t\tname\t\t\"Tahoma\"\n\t}\n}\n")
        self.write_scheme(broken, ["resource/HALFLIFE2.vfont"])
        report = fonts.read(self.game)
        self.assertIn("name", report.unparsed_nodes)
        self.assertEqual(len(report.fonts), 3)

    def test_no_scheme_is_an_error_not_a_traceback(self):
        report = fonts.read(self.game)
        self.assertFalse(report.parsed)
        self.assertIn("no UI scheme found", report.error)

    def test_cli_exits_non_zero_for_a_vfont_only_scheme(self):
        self.write_scheme(SCHEME_VFONT, ["resource/HALFLIFE2.vfont",
                                         "resource/hl2crosshairs.vfont"])
        result = subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "fonts", "--root", str(self.game)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot load here", result.stdout)
        self.assertIn("FreeType-only", result.stdout)

    def test_cli_json_carries_the_verdict(self):
        self.write_scheme(SCHEME_VFONT, ["resource/HALFLIFE2.vfont",
                                         "resource/hl2crosshairs.vfont"])
        result = subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "fonts", "--root", str(self.game), "--json"],
            capture_output=True, text=True)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["vfont_only"])
        self.assertEqual(len(payload["fonts"]), 2)
