import unittest

from l4d1ctl import steamid


class ConversionTests(unittest.TestCase):
    def test_account_id_roundtrip(self):
        for account in (0, 1, 2, 4242, 2**32 - 1):
            sid = steamid.from_account_id(account)
            self.assertEqual(steamid.account_id(sid), account)

    def test_known_values(self):
        self.assertEqual(steamid.from_account_id(0), 76561197960265728)
        # STEAM_0:0:1 -> account 2
        self.assertEqual(steamid.parse("STEAM_0:0:1"), 76561197960265730)
        self.assertEqual(steamid.parse("STEAM_0:1:1"), 76561197960265731)
        self.assertEqual(steamid.parse("[U:1:2]"), 76561197960265730)

    def test_render(self):
        sid = steamid.from_account_id(2)
        self.assertEqual(steamid.to_steam2(sid), "STEAM_0:0:1")
        self.assertEqual(steamid.to_steam3(sid), "[U:1:2]")

    def test_parse_formats(self):
        expected = 76561198000000001
        for value in (
            str(expected),
            f"  {expected}  ",
            f'"{expected}"',
            f"https://steamcommunity.com/profiles/{expected}",
            f"https://steamcommunity.com/profiles/{expected}/",
        ):
            self.assertEqual(steamid.parse(value), expected, value)

    def test_invalid(self):
        for value in ("", "12345", "abc", "STEAM_0:5:1", "76561197960265000"):
            with self.assertRaises(steamid.SteamIDError, msg=value):
                steamid.parse(value)
        self.assertIsNone(steamid.try_parse("nope"))
        self.assertIsNone(steamid.try_parse(None))

    def test_wrapper(self):
        sid = steamid.SteamID(steamid.from_account_id(1234))
        self.assertEqual(sid.account_id, 1234)
        self.assertEqual(sid.steam3, "[U:1:1234]")
        self.assertIn(str(sid.steamid64), sid.profile_url)

    def test_bounds(self):
        self.assertTrue(steamid.is_valid(steamid.STEAMID64_BASE))
        self.assertFalse(steamid.is_valid(0))


if __name__ == "__main__":
    unittest.main()
