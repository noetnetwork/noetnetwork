"""The scheduled model switch in the node overlay (testnet/prepare.py, design/MODEL-SWITCH.md)."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from testnet import prepare

CORE = prepare.ROOT / '.upstream/services/core-node/bcore'
SPEC = json.loads((prepare.ROOT / 'mainnet/network.json').read_text())


class ModelSwitchSpecTests(unittest.TestCase):
    def check(self, **changes):
        return prepare.model_switch({**SPEC['modelSwitch'], **changes}, SPEC['asertHeight'])

    def test_mainnet_switches_to_qwen3_14b_at_its_announced_height(self):
        switch = prepare.mainnet()['modelSwitch']
        self.assertEqual((switch['model'], switch['revision']),
                         ('Qwen/Qwen3-14B', '40c069824f4251a91eefaf281ebe4c544efd3e18'))
        profiles = json.loads((prepare.ROOT / 'models/profiles.json').read_text())
        self.assertEqual((profiles['qwen3-14b']['model'], profiles['qwen3-14b']['revision']),
                         (switch['model'], switch['revision']))
        self.assertEqual((switch['height'], switch['targetScale']), (3500, (2, 1)))  # announced by the owner

    def test_height_needs_a_scale_and_both_are_bounded(self):
        self.assertEqual(self.check(height=5000, targetScale=[5, 2])['targetScale'], (5, 2))
        with self.assertRaisesRegex(ValueError, 'together with its height'):
            self.check(height=5000, targetScale=None)
        for bad in (0, 146, 2 ** 31 - 1, True, '5000', 5000.0):
            with self.assertRaisesRegex(ValueError, 'after the ASERT phase-B height'):
                self.check(height=bad, targetScale=[2, 1])
        for bad in ([1, 2], [9, 2], [0, 0], [2], [2.5, 1], [True, 1], '5/2'):
            with self.assertRaisesRegex(ValueError, 'targetScale'):
                self.check(height=5000, targetScale=bad)

    def test_model_identity_is_exact_and_new(self):
        for changes in ({'revision': 'main'}, {'revision': '40C06982' + '0' * 32}, {'model': 'Qwen3-14B'},
                        {'model': 'Qwen/Qwen3-14B"; evil();//'}):
            with self.assertRaisesRegex(ValueError, 'exact model name'):
                self.check(**changes)
        with self.assertRaisesRegex(ValueError, 'different model'):
            self.check(model=prepare.MODEL)


@unittest.skipUnless(CORE.exists(), 'node sources not present')
class ModelSwitchOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {p: (CORE / p).read_text() for p in prepare.FILES}

    def net(self, folder, **switch):
        spec = {**SPEC, 'modelSwitch': {**SPEC['modelSwitch'], **switch}}
        keys = Path(folder) / 'keys.json'
        shutil.copyfile(prepare.ROOT / SPEC['allocationKeys'], keys)
        spec['allocationKeys'] = str(keys)
        path = Path(folder) / 'network.json'
        path.write_text(json.dumps(spec))
        return prepare.mainnet(path)

    def test_no_height_leaves_the_release_node_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            unscheduled = prepare.overlay(self.sources, net=self.net(folder, height=None, targetScale=None))
            spec = {k: v for k, v in SPEC.items() if k != 'modelSwitch'}
            spec['allocationKeys'] = str(Path(folder) / 'keys.json')
            (Path(folder) / 'plain.json').write_text(json.dumps(spec))
            plain = prepare.overlay(self.sources, net=prepare.mainnet(Path(folder) / 'plain.json'))
        self.assertEqual(unscheduled, plain)
        self.assertNotIn('src/modeldb.cpp', unscheduled)
        self.assertIn('DaaV2PhaseCHeight() const { return std::numeric_limits<int>::max(); }',
                      unscheduled['src/consensus/params.h'])

    def test_scheduled_release_hardwires_height_model_and_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            result = prepare.overlay(self.sources, net=self.net(folder, height=4321, targetScale=[5, 2]))
        chain = result['src/kernel/chainparams.cpp']
        block = chain[chain.index('class CTensorRegParams'):]
        block = block[:block.index('\n};')]
        for expected in ('consensus.SwitchModelName = "Qwen/Qwen3-14B";',
                         'consensus.SwitchModelCommit = "40c069824f4251a91eefaf281ebe4c544efd3e18";',
                         'consensus.ModelSwitchHeight = 4321;'):
            self.assertIn(expected, block)
        self.assertNotIn('-modelswitchheight', block + result['src/init.cpp'])  # no node flag moves consensus
        params = result['src/consensus/params.h']
        for expected in ('int DaaV2PhaseCHeight() const { return ModelSwitchHeight; }',
                         'DAAV2_PHASE_C_TARGET_NUM{5};', 'DAAV2_PHASE_C_TARGET_DEN{2};',
                         'return height >= ModelSwitchHeight ? SwitchModelName + "@" + SwitchModelCommit'):
            self.assertIn(expected, params)
        validation = result['src/validation.cpp']
        self.assertIn('"bad-model-for-height"', validation)
        self.assertIn('challenge-scheduled-model-protected', validation)
        # The protection sits before the sole-model count, so it holds with two models registered.
        self.assertLess(validation.index('challenge-scheduled-model-protected'),
                        validation.index('const bool protected_now = count_registered_models() <= 1;'))
        self.assertIn('ModelSwitchScheduled()', result['src/modeldb.cpp'])
        self.assertIn('cons.ModelForHeight(nHeight)', result['src/node/miner.cpp'])
        self.assertIn('cons.ModelForHeight(height)', result['src/net_processing.cpp'])

    def test_test_variant_reads_the_height_from_its_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            net = self.net(folder, height=None, targetScale=None)
            result = prepare.overlay(self.sources, test=True, net=net)
            profile = prepare.profile(self.sources, result, test=True, net=net)
        self.assertIn('consensus.ModelSwitchHeight = ReadDaaV2HeightArg("-modelswitchheight");',
                      result['src/kernel/chainparams.cpp'])
        self.assertIn('argsman.AddArg("-modelswitchheight=<n>"', result['src/init.cpp'])
        self.assertIn(f'DAAV2_PHASE_C_TARGET_NUM{{{prepare.SWITCH_TEST_SCALE[0]}}};', result['src/consensus/params.h'])
        self.assertEqual(profile['modelSwitch'], {'model': 'Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18',
                                                  'height': '-modelswitchheight',
                                                  'targetScale': list(prepare.SWITCH_TEST_SCALE)})

    def test_private_rehearsal_chain_switches_at_its_own_height(self):
        from realwork import prepare as private
        chain, init = (CORE / 'src/kernel/chainparams.cpp').read_text(), (CORE / 'src/init.cpp').read_text()
        switch = {'model': 'Qwen/Qwen3-14B', 'revision': '40c069824f4251a91eefaf281ebe4c544efd3e18', 'height': 12}
        self.assertEqual(private.overlay(chain, init), private.overlay(chain, init, switch=None))
        edited, _ = private.overlay(chain, init, switch=switch)
        self.assertIn('consensus.ModelSwitchHeight = 12;', edited)
        more = prepare.switch_sources({p: (CORE / p).read_text() for p in prepare.SWITCH_FILES},
                                      {**switch, 'targetScale': (1, 1)})
        self.assertEqual(sorted(more), sorted(prepare.SWITCH_FILES))
        self.assertIn('"bad-model-for-height"', more['src/validation.cpp'])
        for bad in ({**switch, 'height': 1}, {**switch, 'model': private.MODEL}, {**switch, 'revision': 'main'}):
            with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, 'rehearsal switch'):
                private.prepare('builder@sha256:' + 'a' * 64, Path(folder) / 'out.tar', switch=bad)

    def test_switch_check_port_matches_the_reset_rule(self):
        from testnet import switch_check as c
        times = {h: 1_000 + 60 * h for h in range(c.END + 1)}
        bits = {h: 0x1f00ffff for h in range(c.END + 1)}
        target = c.set_compact(0x1f00ffff)
        self.assertEqual(c.expected_bits(c.SWITCH, times, bits, (5, 2)), c.get_compact(target * 5 // 2))
        self.assertEqual(c.expected_bits(c.SWITCH, times, bits, (4, 1)), c.get_compact(target * 4))
        # After the switch ASERT runs from the fresh anchor at SWITCH: on schedule, no change.
        on_time = {**times, **{h: times[c.SWITCH - 1] + 600 * (h - c.SWITCH + 1) for h in range(c.SWITCH, c.END + 1)}}
        self.assertEqual(c.expected_bits(c.SWITCH + 10, on_time, bits, (5, 2)), 0x1f00ffff)


if __name__ == '__main__':
    unittest.main()
