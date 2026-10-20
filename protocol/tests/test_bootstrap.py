import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("noet_bootstrap", Path(__file__).resolve().parents[1] / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class ReviewedPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "--quiet")
        (self.source / "config.txt").write_text("original\n")
        (self.source / "other.txt").write_text("unchanged\n")
        self.git("add", ".")
        self.git("-c", "user.name=NOET test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
        (self.source / "config.txt").write_text("reviewed\n")
        self.patch = self.root / "reviewed.patch"
        self.patch.write_text(self.git("diff", "--binary", "HEAD"))
        self.git("checkout", "--", "config.txt")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], text=True)

    def test_applies_once_and_accepts_repeated_bootstrap(self):
        for _ in range(2):
            bootstrap.apply_reviewed_patch(self.source, self.patch)
            self.assertEqual((self.source / "config.txt").read_text(), "reviewed\n")

    def test_rejects_unreviewed_tracked_input(self):
        bootstrap.apply_reviewed_patch(self.source, self.patch, ignore_submodules=True)
        (self.source / "other.txt").write_text("unreviewed\n")
        with self.assertRaisesRegex(SystemExit, "outside the reviewed NOET patch"):
            bootstrap.apply_reviewed_patch(self.source, self.patch, ignore_submodules=True)


if __name__ == "__main__":
    unittest.main()
