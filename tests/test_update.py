import importlib.util
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vendored_fixture", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
sdk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sdk
spec.loader.exec_module(sdk)

class UpdateTests(unittest.TestCase):
    def archive(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "test.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("blender_manifest.toml", 'id="demo"\nversion="1.0.0"\ntype="add-on"')
            archive.writestr("__init__.py", "")
            for name in files:
                archive.writestr(name, "sample")
        return path

    def test_target_and_version_must_match(self):
        path = self.archive(["vendor/demo.py"])
        self.assertEqual(sdk.inspect_package(path, "demo", "1.0.0"), "")
        for target, version in [("other", "1.0.0"), ("demo", "2.0.0")]:
            with self.assertRaises(ValueError):
                sdk.inspect_package(path, target, version)

    def test_unsafe_paths_and_symlinks(self):
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ["../escape", "C:/escape", "a\\escape", ".git/config", "con.txt", "__INIT__.py", link]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                sdk.inspect_package(self.archive([name]), "demo", "1.0.0")

    def test_revisions(self):
        remote = {"version": "1.0.0", "revision": "new"}
        for installed, previous, expected in [
            ("0.9.0", None, True), ("1.0.0", None, False),
            ("1.0.0", "old", True), ("1.0.0", "new", False), ("1.1.0", "old", False)
        ]:
            self.assertEqual(sdk.update_available(installed, previous, remote), expected)
