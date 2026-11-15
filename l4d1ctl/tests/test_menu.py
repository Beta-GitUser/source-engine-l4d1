"""Reading a GameUI layout: what it asks for, and what this engine can build.

The failure this guards against is a menu that comes up short without a word: a
layout naming a control class that the engine does not implement is skipped silently,
so counting entries is the only way to see it.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from l4d1ctl import menu

GAMEMENU = """GameMenu
{
\t"1"
\t{
\t\t"ControlName"\t"CMainMenu"
\t\t"MainMenu"
\t\t{
\t\t\t"1"
\t\t\t{
\t\t\t\t"ControlName"\t"Button"
\t\t\t\t"LabelText"\t"#L4D1_Play"
\t\t\t\t"Command"\t"OpenPlayMenu"
\t\t\t}
\t\t\t"2"
\t\t\t{
\t\t\t\t"ControlName"\t"Button"
\t\t\t\t"LabelText"\t"#L4D1_Options"
\t\t\t\t"Command"\t"OpenOptionsDialog"
\t\t\t}
\t\t\t"3"
\t\t\t{
\t\t\t\t"ControlName"\t"CBackButton"
\t\t\t\t"LabelText"\t"#L4D1_Quit"
\t\t\t\t"Command"\t"Quit"
\t\t\t}
\t\t}
\t}
}
"""

SOURCE = """
class CGameMenu : public vgui::Menu {};
DECLARE_CLASS_SIMPLE( CBackButton, CBaseButton );
"""


class MenuDissectTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="menu-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        self.res = self.dir / "GameMenu.res"
        self.res.write_text(GAMEMENU)
        self.src = self.dir / "src"
        self.src.mkdir()
        (self.src / "buttons.cpp").write_text(SOURCE)

    def test_entries_carry_class_label_and_command(self):
        report = menu.read(self.res, self.src)
        self.assertTrue(report.parsed, report.error)
        # The root block names a class but carries no command; the three leaves are
        # the menu items, in file order, which is also top-to-bottom on screen.
        self.assertEqual(report.entries[0].control, "CMainMenu")
        commands = [e.action for e in report.entries[1:]]
        self.assertEqual(commands, ["OpenPlayMenu", "OpenOptionsDialog", "Quit"])
        self.assertEqual(report.entries[1].label, "#L4D1_Play")

    def test_classes_this_tree_lacks_are_named(self):
        report = menu.read(self.res, self.src)
        self.assertIn("CMainMenu", report.unknown_controls)
        self.assertIn("Button", report.unknown_controls)
        self.assertNotIn("CBackButton", report.unknown_controls)

    def test_a_layout_in_another_shape_is_described_not_crashed(self):
        other = self.dir / "list.res"
        other.write_text('Menu\n{\n\t"Play"\t"open_play"\n}\n')
        report = menu.read(other, self.src)
        self.assertTrue(report.parsed)
        self.assertEqual(report.entries, [])

    def test_cli_reports_missing_classes_with_a_non_zero_exit(self):
        result = subprocess.run(
            [sys.executable, "-m", "l4d1ctl", "menu", str(self.res), "--source", str(self.src)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CMainMenu", result.stdout)

    def test_scan_finds_both_declaration_spellings(self):
        classes = menu.scan_classes(self.src)
        self.assertIn("CGameMenu", classes)
        self.assertIn("CBackButton", classes)
