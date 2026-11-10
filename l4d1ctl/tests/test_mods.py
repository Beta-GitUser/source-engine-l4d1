import tempfile
import unittest
from pathlib import Path

from l4d1ctl import kv, mods, vpk
from l4d1ctl.paths import PortPaths


def make_paths(root: Path) -> PortPaths:
    return PortPaths(root / "left4dead")


def write_manifest(path: Path, *, name="Mod", kind="auto", order=100, enabled=0) -> None:
    root = kv.KVNode("mod")
    root.set_value("name", name)
    root.set_value("kind", kind)
    root.set_value("order", order)
    root.set_value("enabled", enabled)
    kv.write_file(path, [root])


def make_vpk_mod(mod_dir: Path, base="mymod", parts=2) -> None:
    """A real (tiny) split VPK: <base>_dir.vpk plus <base>_000.vpk, and an extra
    unreferenced part so multi-part staging is exercised."""
    mod_dir.mkdir(parents=True, exist_ok=True)
    vpk.build_vpk_v1(mod_dir / f"{base}_dir.vpk",
                     {"materials/custom.vmt": b"VertexLitGeneric {}"}, split=True)
    for index in range(1, max(parts, 1)):
        (mod_dir / f"{base}_{index:03d}.vpk").write_bytes(b"P" * 8)


def make_folder_mod(mod_dir: Path) -> None:
    (mod_dir / "materials").mkdir(parents=True, exist_ok=True)
    (mod_dir / "materials" / "custom.vmt").write_text("x", encoding="utf-8")
    (mod_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (mod_dir / "scripts" / "custom.txt").write_text("y", encoding="utf-8")


class ScanTests(unittest.TestCase):
    def test_scan_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = mods.ModManager(make_paths(Path(tmp)))
            self.assertEqual(manager.scan(), [])

    def test_scan_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            make_vpk_mod(paths.mods_dir / "a_vpk")
            write_manifest(paths.mods_dir / "a_vpk" / "mod.vdf", name="VPK Mod", order=20, enabled=1)
            make_folder_mod(paths.mods_dir / "b_folder")
            write_manifest(paths.mods_dir / "b_folder" / "mod.vdf", name="Folder Mod", order=10)

            known = {m.mod_id: m for m in mods.ModManager(paths).scan()}
            self.assertEqual(known["a_vpk"].resolved_kind, "vpk")
            self.assertEqual(known["b_folder"].resolved_kind, "folder")
            self.assertTrue(known["a_vpk"].enabled)
            self.assertEqual(known["a_vpk"].name, "VPK Mod")

    def test_scan_ignores_staging_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            paths.staged_dir.mkdir(parents=True)
            write_manifest(paths.staged_dir / "mod.vdf", name="should not count")
            self.assertEqual(mods.ModManager(paths).scan(), [])

    def test_scan_without_manifest_notes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            make_folder_mod(paths.mods_dir / "loose")
            mod = mods.ModManager(paths).scan()[0]
            self.assertIn("no mod.vdf", mod.notes)
            self.assertEqual(mod.resolved_kind, "folder")


class MutationTests(unittest.TestCase):
    def test_enable_disable_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            make_vpk_mod(paths.mods_dir / "m")
            write_manifest(paths.mods_dir / "m" / "mod.vdf")
            manager = mods.ModManager(paths)

            manager.set_enabled("m", True)
            root = kv.find(kv.load_file(paths.mods_dir / "m" / "mod.vdf"), "mod")
            self.assertTrue(root.get_bool("enabled"))

            manager.set_enabled("m", False)
            root = kv.find(kv.load_file(paths.mods_dir / "m" / "mod.vdf"), "mod")
            self.assertFalse(root.get_bool("enabled"))

    def test_set_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            make_folder_mod(paths.mods_dir / "m")
            write_manifest(paths.mods_dir / "m" / "mod.vdf")
            manager = mods.ModManager(paths)
            manager.set_order("m", 5)
            self.assertEqual(manager.get("m").order, 5)

    def test_unknown_mod(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = mods.ModManager(make_paths(Path(tmp)))
            with self.assertRaises(mods.ModError):
                manager.get("nope")

    def test_add_folder_and_vpk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            src_folder = root / "src_folder"
            make_folder_mod(src_folder)
            manager = mods.ModManager(paths)
            mod = manager.add(src_folder, name="Imported")
            self.assertTrue((paths.mods_dir / mod.mod_id / "mod.vdf").is_file())
            self.assertEqual(mod.name, "Imported")

            vpk_dir = root / "src_vpk"
            vpk_dir.mkdir()
            make_vpk_mod(vpk_dir, base="pak")
            added = manager.add(vpk_dir / "pak_dir.vpk", mod_id="pakmod")
            self.assertEqual(added.resolved_kind, "vpk")
            self.assertTrue((paths.mods_dir / "pakmod" / "pak_000.vpk").is_file())
            self.assertTrue((paths.mods_dir / "pakmod" / "pak_001.vpk").is_file())

    def test_add_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            manager = mods.ModManager(paths)
            src = root / "src"
            make_folder_mod(src)
            manager.add(src, mod_id="dup")
            with self.assertRaises(mods.ModError):
                manager.add(src, mod_id="dup")

    def test_add_rejects_non_vpk_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = mods.ModManager(make_paths(root))
            file = root / "thing.txt"
            file.write_text("x", encoding="utf-8")
            with self.assertRaises(mods.ModError):
                manager.add(file, mod_id="txt")

    def test_remove_keeps_files_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            make_folder_mod(paths.mods_dir / "m")
            write_manifest(paths.mods_dir / "m" / "mod.vdf", enabled=1)
            manager = mods.ModManager(paths)
            manager.remove("m")
            self.assertFalse(manager.get("m").enabled)
            self.assertTrue((paths.mods_dir / "m").is_dir())

    def test_remove_purge(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            make_folder_mod(paths.mods_dir / "m")
            write_manifest(paths.mods_dir / "m" / "mod.vdf")
            mods.ModManager(paths).remove("m", delete_files=True)
            self.assertFalse((paths.mods_dir / "m").exists())


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = make_paths(self.root)
        self.manager = mods.ModManager(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sync_vpk_stages_prefixed_parts(self):
        make_vpk_mod(self.paths.mods_dir / "mymod")
        write_manifest(self.paths.mods_dir / "mymod" / "mod.vdf", order=20, enabled=1)
        report = self.manager.sync()
        staged = sorted(p.name for p in self.paths.staged_dir.iterdir())
        self.assertEqual(staged, ["020_mymod_000.vpk", "020_mymod_001.vpk", "020_mymod_dir.vpk"])
        self.assertEqual(len(report.added), 3)

    def test_sync_folder_mod(self):
        make_folder_mod(self.paths.mods_dir / "folder")
        write_manifest(self.paths.mods_dir / "folder" / "mod.vdf", order=5, enabled=1)
        self.manager.sync()
        staged = self.paths.staged_dir / "005_folder"
        self.assertTrue((staged / "materials" / "custom.vmt").is_file())
        self.assertTrue((staged / "scripts" / "custom.txt").is_file())

    def test_sync_idempotent(self):
        make_vpk_mod(self.paths.mods_dir / "m")
        write_manifest(self.paths.mods_dir / "m" / "mod.vdf", enabled=1)
        self.manager.sync()
        second = self.manager.sync()
        self.assertEqual(second.added, [])
        self.assertEqual(second.removed, [])

    def test_disable_removes_staged_entries(self):
        make_vpk_mod(self.paths.mods_dir / "m")
        write_manifest(self.paths.mods_dir / "m" / "mod.vdf", enabled=1)
        self.manager.sync()
        self.manager.set_enabled("m", False)
        report = self.manager.sync()
        self.assertTrue(report.removed)
        self.assertEqual(list(self.paths.staged_dir.iterdir()), [])

    def test_load_order_is_numeric_prefix(self):
        make_folder_mod(self.paths.mods_dir / "z_first")
        make_folder_mod(self.paths.mods_dir / "a_second")
        write_manifest(self.paths.mods_dir / "z_first" / "mod.vdf", order=1, enabled=1)
        write_manifest(self.paths.mods_dir / "a_second" / "mod.vdf", order=2, enabled=1)
        self.manager.sync()
        staged = sorted(p.name for p in self.paths.staged_dir.iterdir())
        self.assertEqual(staged, ["001_z_first", "002_a_second"])

    def test_sync_does_not_delete_foreign_entries(self):
        make_vpk_mod(self.paths.mods_dir / "m")
        write_manifest(self.paths.mods_dir / "m" / "mod.vdf", enabled=1)
        self.paths.staged_dir.mkdir(parents=True)
        foreign = self.paths.staged_dir / "handmade"
        foreign.mkdir()
        self.manager.sync()
        self.assertTrue(foreign.is_dir())

    def test_sync_dry_run(self):
        make_vpk_mod(self.paths.mods_dir / "m")
        write_manifest(self.paths.mods_dir / "m" / "mod.vdf", enabled=1)
        report = self.manager.sync(dry_run=True)
        self.assertTrue(report.added)
        self.assertFalse(self.paths.staged_dir.exists())

    def test_symlink_mode(self):
        paths = make_paths(self.root / "sym")
        manager = mods.ModManager(paths, link_mode="symlink")
        make_folder_mod(paths.mods_dir / "m")
        write_manifest(paths.mods_dir / "m" / "mod.vdf", enabled=1)
        manager.sync()
        staged_file = paths.staged_dir / "100_m" / "materials" / "custom.vmt"
        self.assertTrue(staged_file.is_symlink())

    def test_copy_mode(self):
        paths = make_paths(self.root / "copy")
        manager = mods.ModManager(paths, link_mode="copy")
        make_folder_mod(paths.mods_dir / "m")
        write_manifest(paths.mods_dir / "m" / "mod.vdf", enabled=1)
        manager.sync()
        staged_file = paths.staged_dir / "100_m" / "materials" / "custom.vmt"
        self.assertTrue(staged_file.is_file())
        self.assertFalse(staged_file.is_symlink())


class ValidateTests(unittest.TestCase):
    def test_duplicate_vpk_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            for mod_id in ("a", "b"):
                make_vpk_mod(paths.mods_dir / mod_id, base="pak01")
                write_manifest(paths.mods_dir / mod_id / "mod.vdf")
            issues = mods.ModManager(paths).validate()
            self.assertTrue(any(i.level == "fail" and "collides" in i.message for i in issues))

    def test_vpk_mod_without_dir_vpk(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            (paths.mods_dir / "broken").mkdir(parents=True)
            write_manifest(paths.mods_dir / "broken" / "mod.vdf", kind="vpk")
            issues = mods.ModManager(paths).validate()
            self.assertTrue(any(i.level == "fail" for i in issues))

    def test_duplicate_orders_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            for mod_id in ("a", "b"):
                make_folder_mod(paths.mods_dir / mod_id)
                write_manifest(paths.mods_dir / mod_id / "mod.vdf", order=50)
            issues = mods.ModManager(paths).validate()
            self.assertTrue(any("order 50" in i.message for i in issues))

    def test_clean_install_has_no_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            make_vpk_mod(paths.mods_dir / "m")
            write_manifest(paths.mods_dir / "m" / "mod.vdf", enabled=1)
            manager = mods.ModManager(paths)
            manager.sync()
            self.assertEqual(manager.validate(), [])


if __name__ == "__main__":
    unittest.main()
