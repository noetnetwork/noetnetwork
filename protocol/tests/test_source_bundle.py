import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('source_bundle',Path(__file__).resolve().parents[1]/'source_bundle.py')
bundle=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(bundle)

class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.source=self.root/'source';self.source.mkdir()
        self.git('init','--quiet')
        (self.source/'file').write_text('original\n')
        (self.source/'.gitignore').write_text('secret\n')
        self.git('add','.')
        self.git('-c','user.name=fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','--quiet','-m','fixture')
        self.commit=self.git('rev-parse','HEAD').decode().strip()
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.source),*args])
    def test_only_git_objects_and_reviewed_patch_are_exported(self):
        (self.source/'file').write_text('reviewed\n')
        patch=self.root/'reviewed.patch';patch.write_bytes(self.git('diff','--binary','HEAD'))
        (self.source/'secret').write_text('must not ship')
        (self.source/'untracked').write_text('must not ship either')
        destination=self.root/'export'
        record=bundle.export(self.source,destination,self.commit,patch)
        self.assertEqual((destination/'file').read_text(),'reviewed\n')
        self.assertFalse((destination/'secret').exists());self.assertFalse((destination/'untracked').exists());self.assertFalse((destination/'.git').exists())
        self.assertEqual(record['patchSha256'],bundle.digest(patch))
    def test_rejects_commit_and_unreviewed_changes(self):
        with self.assertRaises(ValueError):bundle.export(self.source,self.root/'export','0'*40)
        (self.source/'file').write_text('unreviewed')
        with self.assertRaises(ValueError):bundle.export(self.source,self.root/'export',self.commit)
    def test_export_and_package_are_deterministic(self):
        a=self.root/'a';b=self.root/'b'
        bundle.export(self.source,a,self.commit);bundle.export(self.source,b,self.commit)
        first=self.root/'a.tar';second=self.root/'b.tar'
        self.assertEqual(bundle.pack(a,first),bundle.pack(b,second))
        with tarfile.open(first) as archive:
            self.assertEqual(archive.getmember('file').mtime,0)
            self.assertEqual(archive.getmember('file').uid,0)
        with self.assertRaises(FileExistsError):bundle.pack(a,first)
    def test_inventory_refuses_links_outside_context(self):
        folder=self.root/'context';folder.mkdir()
        (folder/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):bundle.inventory(folder)
    def test_inventory_preserves_file_digests_and_safe_link_targets(self):
        folder=self.root/'context';folder.mkdir()
        (folder/'file').write_text('source');(folder/'link').symlink_to('file')
        entries=bundle.inventory(folder)
        self.assertEqual(entries[0]['sha256'],bundle.digest(folder/'file'))
        self.assertEqual(entries[1],{'path':'link','type':'symlink','target':'file'})
