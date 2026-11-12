"""The Android workflow and the loader's naming rules must agree.

The module names in CI are not arbitrary: `foundLibraryWithPrefix` in
`tier1/interface.cpp` builds a filename from `"lib" + pModuleName + ".so"` and
stats it inside `APP_LIB_PATH`, and waf writes `lib` + `target` + `.so`. Both are
case-sensitive. A mismatch does not fail anything — it produces a warning about a
missing module and a silent hole in the artifact, which is exactly what happened
with GameUI for several runs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "android.yml"
LOADER = ROOT / "tier1" / "interface.cpp"
GAMEUI_WSCRIPT = ROOT / "gameui" / "wscript"


class ModuleNamingTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_collect_step_does_not_hardcode_the_wrong_case(self):
        """`libgameui.so` never existed; the target is GameUI."""
        collect = self.workflow[self.workflow.index("Collect libraries"):]
        collect = collect[:collect.index("- name: Verify")]
        self.assertNotIn('for name in libclient.so libserver.so liblauncher.so libgameui.so',
                         collect)
        self.assertIn("iname 'libgameui.so'", collect,
                      "collection must match GameUI case-insensitively")

    def test_the_collected_name_keeps_its_capitals(self):
        """The loader stats `libGameUI.so`; copying it as a lowercased name would
        put a file in the artifact that the engine then cannot find."""
        collect = self.workflow[self.workflow.index("Collect libraries"):]
        collect = collect[:collect.index("- name: Verify")]
        self.assertIn('cp "$gameui" "out/$(basename "$gameui")"', collect)

    def test_the_target_name_is_what_the_loader_expects(self):
        wscript = GAMEUI_WSCRIPT.read_text(encoding="utf-8")
        target = re.search(r"PROJECT_NAME\s*=\s*'([^']+)'", wscript).group(1)
        self.assertEqual(target, "GameUI")

        loader = LOADER.read_text(encoding="utf-8", errors="replace")
        # the four candidates foundLibraryWithPrefix tries, in order
        self.assertIn('"%s/" DEFAULT_LIB_PATH "lib%s"', loader)
        self.assertIn('"%s/lib%s"', loader)
        self.assertIn('"%s/%s"', loader)

    def test_the_loader_needs_app_lib_path(self):
        """Without APP_LIB_PATH the Android branch of Sys_LoadModule has no
        directory to search; the carrier has to set it."""
        loader = LOADER.read_text(encoding="utf-8", errors="replace")
        self.assertIn('getenv("APP_LIB_PATH")', loader)
        self.assertIn('getenv("APP_MOD_LIB")', loader)

    def test_docs_no_longer_claim_gameui_is_missing(self):
        building = (ROOT / "docs" / "L4D1" / "BUILDING.md").read_text(encoding="utf-8")
        self.assertNotIn("**`libgameui.so` is not\nproduced.**", building)
        self.assertIn("libGameUI.so", building)


if __name__ == "__main__":
    unittest.main()
