"""The L4D1 server seam must stay wired.

The seam is three files and one macro agreeing with each other:

  game/server/wscript                     'left4dead' lists server_left4dead.vpc
  game/server/server_left4dead.vpc        compiles left4dead/l4d_server_seam.cpp
                                          and defines L4D_OWN_PLAYER
  game/server/hl2/hl2_player.cpp          calls L4D_Server_PlayerSpawned under
                                          #ifdef L4D_OWN_PLAYER

Any one of those can be dropped without breaking the build of the *other* game
targets, which is exactly how a seam silently stops existing. These tests run the
project's own VPC parser, so the check is the same one the build performs.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "game" / "server"
WSCRIPT = SERVER_DIR / "wscript"
SEAM_VPC = SERVER_DIR / "server_left4dead.vpc"
SEAM_CPP = SERVER_DIR / "left4dead" / "l4d_server_seam.cpp"
SEAM_H = SERVER_DIR / "left4dead" / "l4d_server_seam.h"
HL2_PLAYER = SERVER_DIR / "hl2" / "hl2_player.cpp"


def parse_left4dead():
    sys.path.insert(0, str(ROOT / "scripts" / "waifulib"))
    try:
        import vpc_parser
    except ImportError:  # pragma: no cover - the parser lives in the build tree
        raise unittest.SkipTest("vpc_parser not importable")

    class Env:
        DEFINES = ["POSIX", "ANDROID"]
        SUBPROJECT_PATH = [str(SERVER_DIR)]

    cwd = os.getcwd()
    try:
        return vpc_parser.parse_vpcs(
            Env(), ["server_base.vpc", "server_hl2.vpc", "server_left4dead.vpc"], "../..")
    finally:
        os.chdir(cwd)


class SeamWiringTests(unittest.TestCase):
    def test_the_game_target_lists_the_l4d_vpc(self):
        wscript = WSCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "'left4dead': ['server_base.vpc', 'server_hl2.vpc', 'server_left4dead.vpc']",
            wscript)
        # and the hl2 target must not have grown our define
        self.assertIn("'hl2': ['server_base.vpc', 'server_hl2.vpc']", wscript)

    def test_the_parser_produces_our_source_and_define(self):
        result = parse_left4dead()
        self.assertIn("left4dead/l4d_server_seam.cpp", result["sources"])
        self.assertIn("L4D_OWN_PLAYER", result["defines"])
        # the HL2 player file still builds: the seam adds, it does not replace
        self.assertIn("hl2/hl2_player.cpp", result["sources"])

    def test_the_hook_is_called_only_under_the_define(self):
        player = HL2_PLAYER.read_text(encoding="utf-8", errors="replace")
        self.assertIn("L4D_Server_PlayerSpawned( this );", player)
        guarded = player[:player.index("L4D_Server_PlayerSpawned( this );")]
        self.assertGreater(guarded.rfind("#ifdef L4D_OWN_PLAYER"),
                           guarded.rfind("#endif"),
                           "the call must sit inside #ifdef L4D_OWN_PLAYER")

    def test_the_seam_does_not_invent_gameplay(self):
        """It is a marker, not a ruleset: no balance numbers, no L4D constants."""
        source = SEAM_CPP.read_text(encoding="utf-8")
        body = source[source.index("void L4D_Server_PlayerSpawned"):]
        body = "\n".join(line for line in body.splitlines()
                         if not line.strip().startswith("//"))
        for forbidden in ("SetHealth", "SetMaxHealth", "GiveNamedItem",
                          "IncRefCount", "m_iHealth =", "CBaseMultiplayerPlayer"):
            self.assertNotIn(forbidden, body,
                             f"the seam started changing gameplay: {forbidden}")

    def test_the_seam_reports_which_module_is_running(self):
        """The point of the log line: a reader can tell the bootstrap from L4D."""
        self.assertIn("L4D bootstrap", SEAM_CPP.read_text(encoding="utf-8"))

    def test_header_takes_a_forward_declaration_only(self):
        """Including hl2_player.h in the header would drag the whole HL2 player
        into every file that only needs to call the hook."""
        header = SEAM_H.read_text(encoding="utf-8")
        self.assertIn("class CHL2_Player;", header)
        self.assertNotIn('#include "hl2_player.h"', header)


if __name__ == "__main__":
    unittest.main()
