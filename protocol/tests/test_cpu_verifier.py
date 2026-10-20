import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare_verifier_cpu as prepare
from cpu.exercise import read_context, exercise, native_target


class CpuVerifierTests(unittest.TestCase):
    def test_native_adjusted_target_is_exactly_bound_to_header(self):
        for bits,value in ((0x01010000,1),(0x02008000,128),(0x03012345,0x12345),
                           (0x1d00ffff,0xffff<<(8*26)),(0x207fffff,0x7fffff<<(8*29))):
            header='00'*72+bits.to_bytes(4,'little').hex()
            self.assertEqual(native_target(header,f'{value:064x}'),f'{value:064x}')
            with self.assertRaises(ValueError):native_target(header,f'{value+1:064x}')
        for bits in (0,0x01000001,0x20800001,0x21010000,0xff7fffff):
            with self.subTest(bits=bits),self.assertRaises(ValueError):
                native_target('00'*72+bits.to_bytes(4,'little').hex(),'f'*64)
        for header,target in (('00'*75,'0'*64),('00'*76,'0'*63),(True,'0'*64),('00'*76,None)):
            with self.assertRaises(ValueError):native_target(header,target)

    def test_live_profile_refuses_unscaled_registered_difficulty(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'output'
            for difficulty in (True, 1, 999999, 1000001, 60_000_000, 2**63):
                with self.subTest(difficulty=difficulty), self.assertRaisesRegex(ValueError, 'normalizer'):
                    exercise(Path('missing'), Path('missing'), output,
                             node_job={}, registered_difficulty=difficulty)
                self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, 'normalizer'):
                exercise(Path('missing'), Path('missing'), output, registered_difficulty=1_000_000)

    def test_invalid_generation_profiles_fail_before_starting_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'output'
            cases = [(None, 4, 260), ({'header_prefix':'00'*76}, 4, 260),
                     (None, 2, 2052), (None, 3, 260), (None, 2, 261)]
            for context, version, tokens in cases:
                with self.subTest(version=version, tokens=tokens):
                    with self.assertRaises(ValueError):
                        exercise(Path('missing'), Path('missing'), output, context, version, tokens)
                    self.assertFalse(output.exists())

    def test_only_immutable_builder_is_exported(self):
        for image in ('image:latest', 'sha256:'+'a'*63, 'sha256:'+'a'*64+'\n'):
            with self.assertRaises(ValueError), patch.object(prepare.subprocess, 'check_output', side_effect=AssertionError('source access')):
                prepare.prepare(image, Path('unused.tar'))

    def test_export_is_deterministic_and_guarded(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = [Path(folder)/name for name in ('first.tar', 'second.tar')]
            builder = 'sha256:'+'a'*64
            self.assertEqual(prepare.prepare(builder, first), prepare.prepare(builder, second))
            with tarfile.open(first) as archive:
                names = archive.getnames()
                self.assertEqual({name[5:] for name in names if name.startswith('noet/')}, set(prepare.HOST_MODULES))
                self.assertFalse(any('/.git/' in name or '.env' == Path(name).name for name in names))
                source = archive.extractfile('services/verification-api/src/proof_verifier.py').read().decode()
                self.assertIn('approved_pretrained(AutoModelForCausalLM', source)
                self.assertNotIn('.from_pretrained(', source)
                service = archive.extractfile('services/verification-api/src/main.py').read().decode()
                self.assertIn('REMOTE_VERIFY_ENABLED = False', service)
                self.assertIn('self.admission.admit(message)', service)
                self.assertIn('shared-utils/chiavdf/src/python_bindings/fastvdf.cpp', names)
                info = json.load(archive.extractfile('NOET-BUILD-INPUTS.json'))
                self.assertEqual(info['nativeBuilder'], builder)
                self.assertFalse(info['productionReady'])
            with self.assertRaises(FileExistsError):
                prepare.prepare(builder, first)

    def test_wrong_builder_identity_or_architecture_prevents_build(self):
        for results in (['sha256:'+'b'*64], ['sha256:'+'a'*64, 'amd64']):
            with patch.object(prepare.subprocess, 'check_output', side_effect=results), patch.object(prepare.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Native builder differs'):
                    prepare.build(Path('does-not-exist'), 'sha256:'+'a'*64, 'local:builder', 'local:output')
                run.assert_not_called()

    def test_build_cannot_overwrite_builder(self):
        with patch.object(prepare.subprocess, 'check_output', side_effect=AssertionError('Docker access')):
            with self.assertRaisesRegex(ValueError, 'must not replace'):
                prepare.build(Path('unused'), 'sha256:'+'a'*64, 'local:builder', 'local:builder')

    def test_context_identity_and_tag_change_are_rejected(self):
        expected = 'sha256:'+'a'*64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'context'; root.mkdir()
            record = root/'NOET-BUILD-INPUTS.json'
            for identity in (expected, 'sha256:'+'b'*64):
                record.write_text(json.dumps({'nativeBuilder': identity}))
                output = Path(folder)/(identity[-1]+'.tar')
                prepare.pack(root, output)
                with patch.object(prepare.subprocess, 'check_output', side_effect=[expected, 'arm64', 'sha256:'+'c'*64]), patch.object(prepare.subprocess, 'run') as run:
                    with self.assertRaisesRegex(ValueError, 'changed during build' if identity == expected else 'Context builder identity'):
                        prepare.build(output, expected, 'local:builder', 'local:output')
                    self.assertEqual(run.call_count, int(identity == expected))

    def test_context_validation_is_bounded_and_exact(self):
        valid = {'block_hash': 'ab'*32, 'header_prefix': '04000000'+'ab'*32+'00'*40,
                 'vdf': '00'*200, 'tick': 10000}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'context.json'
            path.write_text(json.dumps(valid))
            self.assertEqual(read_context(path), valid)
            invalid = [[], {**valid, 'extra': 1}, {**valid, 'header_prefix': '00'*76},
                       {**valid, 'block_hash': '00'*32}, {**valid, 'vdf': 'AA'*200}]
            invalid += [{**valid, 'tick': value} for value in (True, '10000', 0, -1, 100001, 1.5)]
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError): read_context(path)
            path.write_text(' '*4097)
            with self.assertRaisesRegex(ValueError, 'too large'): read_context(path)
