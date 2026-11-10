import tempfile
import unittest
from pathlib import Path

from l4d1ctl import kv

LIBRARY_FOLDERS_V2 = '''
"libraryfolders"
{
	"0"
	{
		"path"		"/home/me/.steam/steam"
		"label"		""
		"apps"
		{
			"500"		"12345"
			"220"		"6789"
		}
	}
	"1"
	{
		"path"		"/mnt/games/SteamLibrary"
		"apps"		{ "500"		"12345" }
	}
}
'''

APPMANIFEST = '''
"AppState"
{
	"appid"		"500"
	"Universe"		"1"
	"name"		"Left 4 Dead"
	"StateFlags"		"4"
	"installdir"		"Left 4 Dead"
	"SizeOnDisk"		"12884901888"
	"buildid"		"9255"
	"InstalledDepots"
	{
		"500"
		{
			"manifest"		"5031779478644304717"
			"size"		"1234"
		}
	}
}
'''

LOGIN_USERS = '''
"users"
{
	"76561197960265730"
	{
		"AccountName"		"oldaccount"
		"PersonaName"		"Old Name"
		"RememberPassword"		"1"
		"Timestamp"		"1600000000"
		"MostRecent"		"0"
	}
	"76561198000000001"
	{
		"AccountName"		"activeuser"
		"PersonaName"		"Zoey Fan"
		"RememberPassword"		"1"
		"Timestamp"		"1700000000"
		"MostRecent"		"1"
	}
}
'''


class ParseTests(unittest.TestCase):
    def test_nested_sections_and_values(self):
        nodes = kv.loads(APPMANIFEST)
        state = kv.find(nodes, "AppState")
        self.assertIsNotNone(state)
        self.assertEqual(state.get("name"), "Left 4 Dead")
        self.assertEqual(state.get_int("SizeOnDisk"), 12884901888)
        self.assertEqual(state.get("buildid"), "9255")
        depots = state.find("installeddepots")  # case-insensitive
        self.assertIsNotNone(depots)
        self.assertEqual(depots.find("500").get("size"), "1234")

    def test_bare_tokens_and_comments(self):
        text = """
        // leading comment
        GameInfo
        {
            game "Left 4 Dead"   // trailing comment
            type multiplayer_only
        }
        """
        nodes = kv.loads(text)
        root = kv.find(nodes, "gameinfo")
        self.assertEqual(root.get("game"), "Left 4 Dead")
        self.assertEqual(root.get("type"), "multiplayer_only")

    def test_platform_tokens_recorded_not_applied(self):
        nodes = kv.loads('"a" "b" [$WIN32]\n"c" "d" [$X360]\n')
        self.assertEqual(nodes[0].platform, "WIN32")
        self.assertEqual(nodes[0].value, "b")
        self.assertEqual(nodes[1].platform, "X360")

    def test_escapes(self):
        nodes = kv.loads(r'"name" "Quote\" and backslash\\ and newline\n"')
        self.assertEqual(nodes[0].value, 'Quote" and backslash\\ and newline\n')

    def test_unbalanced_braces_raise(self):
        with self.assertRaises(kv.KVParseError):
            kv.loads('"a" { "b" "c"')

    def test_stray_close_brace_ignored(self):
        nodes = kv.loads('}\n"a" "1"\n')
        self.assertEqual(nodes[0].value, "1")

    def test_roundtrip(self):
        nodes = kv.loads(APPMANIFEST)
        reparsed = kv.loads(kv.dumps(nodes))
        self.assertEqual(kv.find(reparsed, "AppState").get("name"), "Left 4 Dead")

    def test_library_folders_v2(self):
        root = kv.find(kv.loads(LIBRARY_FOLDERS_V2), "libraryfolders")
        paths = [child.find("path").value for child in root.children]
        self.assertEqual(paths, ["/home/me/.steam/steam", "/mnt/games/SteamLibrary"])


class NodeTests(unittest.TestCase):
    def test_set_value_and_remove(self):
        node = kv.KVNode("root")
        node.set_value("Name", "x")
        node.set_value("name", "y")  # case-insensitive replace
        self.assertEqual(len(node.children), 1)
        self.assertEqual(node.get("NAME"), "y")
        self.assertTrue(node.remove("name"))
        self.assertFalse(node.remove("name"))

    def test_get_int_hex_and_invalid(self):
        node = kv.KVNode("root")
        node.set_value("hex", "0x10")
        node.set_value("bad", "nope")
        self.assertEqual(node.get_int("hex"), 16)
        self.assertEqual(node.get_int("bad", 7), 7)

    def test_walk_and_flatten(self):
        nodes = kv.loads(APPMANIFEST)
        keys = [n.key for n in nodes[0].walk()]
        self.assertIn("manifest", keys)
        self.assertEqual(len(kv.flatten(nodes, "manifest")), 1)

    def test_load_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appmanifest_500.acf"
            path.write_bytes(b'\xef\xbb\xbf"AppState" { "appid" "500" }')
            nodes = kv.load_file(path)
            self.assertEqual(kv.find(nodes, "AppState").get("appid"), "500")


if __name__ == "__main__":
    unittest.main()
