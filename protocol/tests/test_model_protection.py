"""Model mutation and privilege boundaries for restricted container hosts."""
from contextlib import ExitStack
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from noet import model_runtime as runtime


class ModelProtectionTests(unittest.TestCase):
    root = Path('/models/pinned')
    files = [{'path':'weights.safetensors'}]
    status = 'Uid:\t10001 10001 10001 10001\nNoNewPrivs:\t1\nCapEff:\t0\nCapPrm:\t0\nCapAmb:\t0\n'

    def environment(self, *, uid=10001, euid=10001, status=None, changed=None, writable=None):
        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, {'NOET_MODEL_PROTECTION':'root-owned'}))
        stack.enter_context(patch.object(runtime.os, 'getuid', return_value=uid))
        stack.enter_context(patch.object(runtime.os, 'geteuid', return_value=euid))
        stack.enter_context(patch.object(Path, 'read_text', return_value=status or self.status))
        stack.enter_context(patch.object(runtime.os, 'access', side_effect=lambda path,*a,**kw: path==writable))
        def info(path):
            mode=stat.S_IFREG|0o644 if path.name=='weights.safetensors' else stat.S_IFDIR|0o755
            return (changed[1] if changed and path==changed[0] else SimpleNamespace(st_uid=0,st_mode=mode))
        stack.enter_context(patch.object(Path,'lstat',info))
        return stack

    def test_root_owned_snapshot_requires_explicit_unprivileged_mode(self):
        with self.environment():runtime.require_snapshot_protection(self.root,self.files)
        with patch.dict(os.environ, {'NOET_MODEL_PROTECTION':'disabled'}),self.assertRaises(ValueError):
            runtime.require_snapshot_protection(self.root,self.files)

    def test_root_effective_and_saved_privilege_are_refused(self):
        for uid,euid in ((0,0),(0,10001),(10001,0)):
            with self.subTest(uid=uid,euid=euid),self.environment(uid=uid,euid=euid),self.assertRaises(ValueError):
                runtime.require_snapshot_protection(self.root,self.files)
        for status in (self.status.replace('10001 10001 10001 10001','10001 10001 0 10001'),
                       self.status.replace('NoNewPrivs:\t1','NoNewPrivs:\t0')):
            with self.environment(status=status),self.assertRaises(ValueError):
                runtime.require_snapshot_protection(self.root,self.files)

    def test_any_capability_set_is_refused(self):
        for field in ('CapEff','CapPrm','CapAmb'):
            with self.subTest(field=field),self.environment(status=self.status.replace(field+':\t0',field+':\t2')),self.assertRaises(ValueError):
                runtime.require_snapshot_protection(self.root,self.files)

    def test_writable_ancestor_owner_file_and_links_are_refused(self):
        for path in (Path('/'),Path('/models'),self.root,self.root/'weights.safetensors'):
            directory=path.name!='weights.safetensors'
            for mode,uid in ((stat.S_IFDIR|0o777,0),(stat.S_IFREG|0o666,0),
                             ((stat.S_IFDIR if directory else stat.S_IFREG)|0o755,10001),
                             (stat.S_IFLNK|0o777,0)):
                with self.subTest(path=path,mode=mode,uid=uid),self.environment(changed=(path,SimpleNamespace(st_uid=uid,st_mode=mode))),self.assertRaises(ValueError):
                    runtime.require_snapshot_protection(self.root,self.files)

    def test_acl_write_access_is_refused_even_with_protected_mode_bits(self):
        with self.environment(writable=self.root/'weights.safetensors'),self.assertRaises(ValueError):
            runtime.require_snapshot_protection(self.root,self.files)
