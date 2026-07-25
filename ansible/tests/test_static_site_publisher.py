#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO_ROOT
    / "apps"
    / "static-site"
    / "ansible"
    / "roles"
    / "static-site-runtime"
    / "files"
    / "static-site-publisher"
)


def load_module():
    loader = SourceFileLoader("static_site_publisher", str(SCRIPT))
    spec = importlib.util.spec_from_loader("static_site_publisher", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class StaticSitePublisherTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def config(self, root):
        return self.mod.Config(
            incoming_root=root / "incoming",
            public_root=root / "public",
            repo_cache_root=root / "repo",
            git_repo="git@github.com:klokast/klokast-site.git",
            git_branch="main",
            git_source_dir="www",
            git_ssh_config=root / "ssh_config",
            interval_seconds=1,
            max_bytes=4096,
        )

    def test_publishes_git_www_tree_to_public_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self.config(root)
            public = root / "public"
            public.mkdir()
            (public / "old.txt").write_text("stale", encoding="utf-8")

            publisher = self.mod.Publisher(config)
            publisher.ensure_config_files = lambda: None
            publisher.ensure_repo = lambda: config.repo_cache_root.mkdir()
            publisher.fetch_commit = lambda: "a" * 40

            def extract_source(_commit, staging):
                source = staging / "www"
                (source / "pollen").mkdir(parents=True)
                (source / "nvidia-codesign").mkdir()
                (source / "assets").mkdir()
                (source / "index.html").write_text("<!doctype html><html>Home</html>", encoding="utf-8")
                (source / "pollen" / "index.html").write_text(
                    "<!doctype html><html>Pollen</html>", encoding="utf-8"
                )
                (source / "nvidia-codesign" / "index.html").write_text(
                    "<!doctype html><html>Codesign</html>", encoding="utf-8"
                )
                (source / "assets" / "main.css").write_text("body{color:#111}", encoding="utf-8")
                return source

            publisher.extract_source = extract_source
            publisher.publish_once()

            self.assertFalse((public / "old.txt").exists())
            self.assertIn("Home", (public / "index.html").read_text(encoding="utf-8"))
            self.assertIn("Pollen", (public / "pollen" / "index.html").read_text(encoding="utf-8"))
            self.assertIn(
                "Codesign",
                (public / "nvidia-codesign" / "index.html").read_text(encoding="utf-8"),
            )
            self.assertEqual((public / "assets" / "main.css").read_text(encoding="utf-8"), "body{color:#111}")
            self.assertEqual((config.repo_cache_root / ".published-commit").read_text(encoding="utf-8"), "a" * 40 + "\n")

    def test_rejects_unsafe_static_site_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "www"
            root.mkdir()
            (root / ".secret").write_text("secret", encoding="utf-8")

            with self.assertRaises(self.mod.PublishError):
                self.mod.validate_tree(root, 4096)

    def test_allows_well_known_static_site_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "www"
            (root / ".well-known").mkdir(parents=True)
            (root / ".well-known" / "security.txt").write_text("Contact: mailto:security@example.invalid\n", encoding="utf-8")

            self.mod.validate_tree(root, 4096)

    def test_rejects_non_html_index_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "www"
            root.mkdir()
            (root / "index.html").write_text("not html", encoding="utf-8")

            with self.assertRaises(self.mod.PublishError):
                self.mod.validate_tree(root, 4096)

    def test_rejects_unsafe_source_dir(self):
        with self.assertRaises(self.mod.PublishError):
            self.mod.validate_source_dir("../www")


if __name__ == "__main__":
    unittest.main()
