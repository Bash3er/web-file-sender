import json
import os
import tempfile
import unittest
from pathlib import Path

from core.access_control import allowed_path
from core.file_manager import resolve_virtual_path

class AccessControlTests(unittest.TestCase):
    def test_only_shared_paths_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "share"
            root.mkdir()
            file = root / "hello.txt"
            file.write_text("hi")
            other = Path(temp) / "private.txt"
            other.write_text("secret")
            config = {"shares":[{"path":str(root),"name":"Share"}],"rules":[],"hidden_files":False}
            self.assertTrue(allowed_path(file, config))
            self.assertFalse(allowed_path(other, config))
            self.assertEqual(resolve_virtual_path("Share/hello.txt", config), file.resolve())
            with self.assertRaises(PermissionError):
                resolve_virtual_path("Share/../private.txt", config)

    def test_deny_overrides_share(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "share"
            root.mkdir()
            secret = root / "secret.txt"
            secret.write_text("secret")
            config = {"shares":[{"path":str(root),"name":"Share"}],
                      "rules":[{"path":str(secret),"action":"deny"}],"hidden_files":False}
            self.assertFalse(allowed_path(secret, config))

    def test_individual_file_share_is_listed(self):
        from core.file_manager import root_items
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "single.txt"
            file.write_text("hello")
            config = {"shares":[{"path":str(file),"name":"Single"}],"rules":[],"hidden_files":False}
            items = root_items(config)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["type"], "file")
            self.assertEqual(items[0]["path"], "Single")

    def test_excluded_child_does_not_hide_parent(self):
        from core.file_manager import root_items, list_directory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "share"
            root.mkdir()
            (root / "public.txt").write_text("public")
            secret = root / "secret.txt"
            secret.write_text("secret")
            config = {"shares":[{"path":str(root),"name":"Share"}],
                      "rules":[{"path":str(secret),"action":"deny"}],"hidden_files":False}
            self.assertEqual(len(root_items(config)), 1)
            items = list_directory(root, config)
            self.assertEqual([item["name"] for item in items], ["public.txt"])

if __name__ == "__main__":
    unittest.main()
