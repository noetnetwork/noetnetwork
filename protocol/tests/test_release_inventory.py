import importlib.util
import json
from pathlib import Path
import unittest
SPEC=importlib.util.spec_from_file_location('release_inventory',Path(__file__).resolve().parents[1]/'release_inventory.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)

class ReleaseInventoryTests(unittest.TestCase):
    def test_collection_pins_image_and_disables_network_writes_and_entrypoint(self):
        calls=[];identity='sha256:'+'a'*64
        def run(*args):
            calls.append(args)
            if args[:2]==('image','inspect'):return json.dumps([{'Id':identity,'Os':'linux','Architecture':'arm64'}])
            self.assertIn(identity,args);self.assertNotIn('candidate:latest',args)
            for control in ('--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges'):self.assertIn(control,args)
            if 'dpkg-query' in args:return 'zlib1g\t1.0\tarm64\n'
            return '\n'.join('b'*64+'  '+name for name in module.BINARIES)
        result=module.collect('candidate:latest',run)
        self.assertEqual(len(calls),3);self.assertEqual(result['imageId'],identity)
        self.assertFalse(result['signed']);self.assertFalse(result['productionReady'])
    def test_package_parser_rejects_missing_and_duplicate_fields(self):
        for text in ('','a\tb','a\tb\tc\na\tb\tc','a\t\tc'):
            with self.assertRaises(ValueError):module.packages(text)
    def test_binary_parser_requires_exact_executables_and_valid_digests(self):
        valid='\n'.join('b'*64+'  '+name for name in module.BINARIES)
        self.assertEqual(len(module.binaries(valid)),4)
        for text in (valid+'\n'+'c'*64+'  /tmp/other',valid.replace('b'*64,'x'*64),valid.splitlines()[0],valid+'\n'+valid.splitlines()[0]):
            with self.assertRaises(ValueError):module.binaries(text)
    def test_unexpected_image_identity_rejected_before_execution(self):
        for image in ({'Id':'tag','Os':'linux','Architecture':'arm64'},{'Id':'sha256:'+'a'*64,'Os':'windows','Architecture':'amd64'}):
            calls=[]
            def run(*args):calls.append(args);return json.dumps([image])
            with self.assertRaises(ValueError):module.collect('image',run)
            self.assertEqual(len(calls),1)
