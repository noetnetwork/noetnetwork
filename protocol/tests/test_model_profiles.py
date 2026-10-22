"""Candidate model profiles stay consistent with their manifests and the mainnet pins."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from noet.model_artifacts import validate
from realwork.prepare import overlay

ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / 'models/profiles.json').read_text())
MAINNET = PROFILES['qwen3-0.6b']
MAINNET_ID = MAINNET['model'] + '@' + MAINNET['revision']
CORE = ROOT / '.upstream/services/core-node/bcore'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelProfileTests(unittest.TestCase):
    def test_every_profile_matches_its_pinned_manifest(self):
        tokenizer = None
        for name, profile in PROFILES.items():
            raw = (ROOT / profile['manifest']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), profile['manifestSha256'], name)
            manifest = validate(json.loads(raw))
            self.assertEqual((manifest['model'], manifest['revision'], manifest['license']),
                             (profile['model'], profile['revision'], profile['license']), name)
            self.assertRegex(profile['gguf'], r'^[a-z0-9.-]+-bf16\.gguf$')
            self.assertTrue(profile['ggufSha256'] is None or re.fullmatch(r'[a-f0-9]{64}', profile['ggufSha256']))
            # Every candidate shares the mainnet tokenizer, so gateway tasks and
            # the vocabulary used for answers are unchanged by a model switch.
            files = {row['path']: row['sha256'] for row in manifest['files']}
            shared = tuple(files[n] for n in ('tokenizer.json', 'vocab.json', 'merges.txt', 'tokenizer_config.json'))
            tokenizer = tokenizer or shared
            self.assertEqual(shared, tokenizer, name)
        self.assertEqual([n for n, p in PROFILES.items() if p['status'] == 'mainnet'], ['qwen3-0.6b'])

    def test_mainnet_profile_matches_every_hard_pin(self):
        self.assertEqual(load('miner', 'miner/noet_miner.py').MODEL, MAINNET_ID)
        self.assertEqual(load('genesis', 'mainnet/genesis.py').MODEL, MAINNET_ID)
        stage = load('stage', 'gpu/stage.py')
        self.assertEqual((stage.GGUF_SHA256, stage.MANIFEST_SHA256), (MAINNET['ggufSha256'], MAINNET['manifestSha256']))
        # Without a model.json these defaults are what the live pod runs.
        expected = {'gpu/run.sh': (MAINNET['manifestSha256'], MAINNET_ID),
                    'gpu/native.py': (MAINNET['ggufSha256'], MAINNET_ID),
                    'miner/convert.sh': (MAINNET['ggufSha256'],)}
        for path, values in expected.items():
            for value in values:
                self.assertIn(value, (ROOT / path).read_text(), path)
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(load('exercise', 'cpu/exercise.py').MODEL, MAINNET_ID)

    def test_switch_model_pins_match_its_profile(self):
        # design/MODEL-SWITCH.md: the miner converts and checks Qwen3-14B with these pins.
        switch = json.loads((ROOT / 'models/profiles.json').read_text())['qwen3-14b']
        convert = (ROOT / 'miner/convert.sh').read_text()
        for value in (switch['ggufSha256'], switch['manifestSha256'], switch['gguf']):
            self.assertIn(value, convert)
        self.assertEqual(load('miner', 'miner/noet_miner.py').MODELS[switch['model'] + '@' + switch['revision']]['sha256'],
                         switch['ggufSha256'])

    def test_producer_model_comes_only_from_a_well_formed_identifier(self):
        candidate = PROFILES['qwen3-8b']
        with mock.patch.dict('os.environ', {'NOET_MODEL_ID': candidate['model'] + '@' + candidate['revision']}):
            self.assertEqual(load('exercise8b', 'cpu/exercise.py').MODEL, 'Qwen/Qwen3-8B@' + candidate['revision'])
        for bad in ('Qwen/Qwen3-8B', 'Qwen/Qwen3-8B@main', '../x@' + 'a' * 40):
            with mock.patch.dict('os.environ', {'NOET_MODEL_ID': bad}), self.assertRaises(ValueError):
                load('exercise-bad', 'cpu/exercise.py')

    def test_producer_gpu_defaults_to_the_first_and_takes_only_an_index(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(load('exercise_gpu', 'cpu/exercise.py').PRODUCER_GPU, '0')
        with mock.patch.dict('os.environ', {'NOET_PRODUCER_GPU': '1'}):
            self.assertEqual(load('exercise_gpu1', 'cpu/exercise.py').PRODUCER_GPU, '1')
        for bad in ('', '0,1', '10', 'all'):
            with mock.patch.dict('os.environ', {'NOET_PRODUCER_GPU': bad}), self.assertRaises(ValueError):
                load('exercise_gpu_bad', 'cpu/exercise.py')

    @unittest.skipIf(Path('/workspace/noet-gpu').exists(), 'run.sh would start the real worker here')
    def test_run_sh_keeps_gpu_0_by_default_and_refuses_other_gpu_values(self):
        env = {'PATH': os.environ['PATH']}
        # Valid values get as far as the (absent) verifier tree; bad ones stop first.
        for extra in ({}, {'NOET_VERIFIER_GPU': '1', 'NOET_PRODUCER_GPU': '0'}):
            result = subprocess.run(['bash', str(ROOT / 'gpu/run.sh')], env=env | extra, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('verification-api/src', result.stderr)
        for extra in ({'NOET_VERIFIER_GPU': '0,1'}, {'NOET_PRODUCER_GPU': 'x'}, {'NOET_VERIFIER_GPU': '01'}):
            result = subprocess.run(['bash', str(ROOT / 'gpu/run.sh')], env=env | extra, capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr.strip()), (2, 'GPU index must be a single digit'))
        text = (ROOT / 'gpu/run.sh').read_text()
        self.assertIn('CUDA_VISIBLE_DEVICES=$VERIFIER_GPU NOET_PRODUCER_GPU=$PRODUCER_GPU', text)

    def test_host_plan_fits_each_model_to_the_gpus_it_needs(self):
        plan = load('plan_host', 'gpu/plan_host.py')
        mib = 1024 * 1024
        a5000, a6000, a100, h200 = (n * mib for n in (24564, 49140, 81920, 143771))
        weights = {name: sum(row['bytes'] for row in json.loads((ROOT / p['manifest']).read_text())['files'])
                   for name, p in PROFILES.items()}
        disk = 10**13
        fits = [('qwen3-0.6b', [a5000], (0, 0)), ('qwen3-8b', [a100], (0, 0)), ('qwen3-8b', [a6000, a6000], (0, 1)),
                ('qwen3-14b', [h200], (0, 0)), ('qwen3-14b', [a100, a100], (0, 1)), ('qwen3-32b', [h200, h200], (0, 1))]
        for name, gpus, expected in fits:
            with self.subTest(name=name, gpus=gpus):
                self.assertEqual(plan.plan(weights[name], gpus, disk, False), expected)
        too_small = [('qwen3-8b', [a6000]), ('qwen3-8b', [a5000, a5000]), ('qwen3-14b', [a100]),
                     ('qwen3-32b', [a100, a100]), ('qwen3-32b', [h200]), ('qwen3-8b', [])]
        for name, gpus in too_small:
            with self.subTest(name=name, gpus=gpus), self.assertRaisesRegex(ValueError, 'needs one GPU'):
                plan.plan(weights[name], gpus, disk, False)
        need = plan.needs(weights['qwen3-32b'])['disk']
        with self.assertRaisesRegex(ValueError, 'free disk'):
            plan.plan(weights['qwen3-32b'], [h200, h200], need - 1, False)
        self.assertEqual(plan.plan(weights['qwen3-32b'], [h200, h200], 0, True), (0, 1))

    def test_host_plan_reads_nvidia_smi_and_records_its_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, rows, code in (('two', '0, 81920\n1, 81920\n', 0), ('one', '0, 49140\n', 1)):
                fake = folder / name / 'nvidia-smi'
                fake.parent.mkdir()
                fake.write_text(f"#!/bin/sh\nprintf '{rows}'\n")
                fake.chmod(0o755)
                out = folder / (name + '.json')
                result = subprocess.run([sys.executable, str(ROOT / 'gpu/plan_host.py'), '--manifest',
                                         str(ROOT / PROFILES['qwen3-8b']['manifest']), '--disk', folder, '--converted',
                                         '--out', out], env={'PATH': f'{fake.parent}:{os.environ["PATH"]}'},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                record = json.loads(out.read_text())
                if code == 0:
                    self.assertEqual((result.stdout, record['producerGpu'], record['verifierGpu']), ('0 1\n', 0, 1))
                else:
                    self.assertIn('too small', result.stderr)
                    self.assertIn('needs one GPU', record['error'])

    def test_private_network_overlay_registers_the_chosen_model(self):
        chain = (CORE / 'src/kernel/chainparams.cpp').read_text()
        init = (CORE / 'src/init.cpp').read_text()
        candidate = PROFILES['qwen3-8b']
        default, _ = overlay(chain, init)
        changed, _ = overlay(chain, init, candidate['model'], candidate['revision'])
        block = changed[changed.index('class CTensorRegParams'):].split('\n};')[0]
        self.assertIn(f'consensus.DefaultModelName = "{candidate["model"]}";', block)
        self.assertIn(f'consensus.DefaultModelCommit = "{candidate["revision"]}";', block)
        self.assertIn(f'consensus.DefaultModelName = "{MAINNET["model"]}";', default)
        differing = [(a, b) for a, b in zip(default.splitlines(), changed.splitlines()) if a != b]
        self.assertEqual(len(default.splitlines()), len(changed.splitlines()))
        self.assertEqual([b.strip() for _, b in differing],
                         [f'consensus.DefaultModelName = "{candidate["model"]}";',
                          f'consensus.DefaultModelCommit = "{candidate["revision"]}";'])

    def test_candidate_download_uses_its_own_revision_and_digest(self):
        fetch = load('cpu_fetch_profiles', 'cpu/fetch_model.py')
        candidate = PROFILES['qwen3-8b']
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                fetch.fetch(Path(folder) / 'm', ROOT / candidate['manifest'], MAINNET['manifestSha256'], 10**11)
            with self.assertRaises(ValueError):
                fetch.fetch(Path(folder) / 'm', ROOT / candidate['manifest'], candidate['manifestSha256'], 2 * 10**9)
            seen = []
            def record(opener, directory, row, origin):
                seen.append(origin + row['path'])
            with mock.patch.object(fetch, 'download_record', record):
                fetch.fetch(Path(folder) / 'm', ROOT / candidate['manifest'], candidate['manifestSha256'], 10**11)
        self.assertTrue(all(url.startswith(f'https://huggingface.co/Qwen/Qwen3-8B/resolve/{candidate["revision"]}/')
                            for url in seen))
        self.assertEqual(len(seen), len(json.loads((ROOT / candidate['manifest']).read_text())['files']))

    def test_manifest_builder_checks_git_blobs_and_lfs_records(self):
        builder = load('candidate', 'models/candidate.py')
        data = b'{"a": 1}\n'
        blob = builder.git_blob_id(data)
        with mock.patch.object(builder, 'fetch', return_value=data):
            row = builder.entry('Qwen/Qwen3-8B', 'b' * 40, {'rfilename': 'config.json', 'size': len(data), 'blobId': blob})
            self.assertEqual(row, {'path': 'config.json', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            with self.assertRaises(ValueError):
                builder.entry('Qwen/Qwen3-8B', 'b' * 40, {'rfilename': 'config.json', 'size': len(data), 'blobId': '0' * 40})
        lfs = {'rfilename': 'model.safetensors', 'size': 5, 'lfs': {'sha256': 'c' * 64, 'size': 5}}
        self.assertEqual(builder.entry('Qwen/Qwen3-8B', 'b' * 40, lfs), {'path': 'model.safetensors', 'bytes': 5, 'sha256': 'c' * 64})
        with self.assertRaises(ValueError):
            builder.entry('Qwen/Qwen3-8B', 'b' * 40, dict(lfs, size=6))
        with self.assertRaises(ValueError):
            builder.entry('Qwen/Qwen3-8B', 'b' * 40, {'rfilename': '../x', 'size': 1, 'blobId': '0' * 40})

    def test_stage_refuses_unknown_profiles_and_uploaded_candidate_weights(self):
        stage = load('stage_profiles', 'gpu/stage.py')
        with self.assertRaises(ValueError):
            stage.load_profile('qwen3-999b')
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            stage.stage(Path(folder) / 'out', 'builder@sha256:' + 'a' * 64, Path(folder), None, 'qwen3-8b')
        for switch_profile, height in (('qwen3-0.6b', 12), ('qwen3-14b', 1), ('qwen3-14b', None)):
            with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, 'switch rehearsal'):
                stage.stage(Path(folder) / 'out', 'builder@sha256:' + 'a' * 64, None, None, 'qwen3-0.6b',
                            switch_profile, height)


if __name__ == '__main__':
    unittest.main()
