import json
from pathlib import Path
import shutil
import tempfile
import unittest

from testnet import prepare

CORE = prepare.ROOT / '.upstream/services/core-node/bcore'


class MainnetTests(unittest.TestCase):
    def spec(self, folder, **changes):
        spec = {**json.loads((prepare.ROOT / 'mainnet/network.json').read_text()), **changes}
        path = Path(folder) / 'network.json'
        path.write_text(json.dumps(spec))
        return path

    def test_refuses_without_genesis_or_real_signers(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'genesis is not set'):
                prepare.mainnet(self.spec(folder, genesisTime=None, genesisNonce=None))
            with self.assertRaisesRegex(ValueError, 'signers are not set'):
                prepare.mainnet(self.spec(folder, genesisTime=1800000000, genesisNonce=0,
                                          allocationKeys=str(Path(folder) / 'missing.json')))

    def test_published_genesis_meets_its_target(self):
        import hashlib
        import struct
        spec = json.loads((prepare.ROOT / 'mainnet/network.json').read_text())
        if spec['genesisTime'] is None:
            self.skipTest('mainnet genesis not fixed yet')
        merkle = bytes.fromhex('4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b')[::-1]
        header = struct.pack('<I', 1) + bytes(32) + merkle + struct.pack('<III', spec['genesisTime'], 0x207fffff,
                                                                          spec['genesisNonce'])
        short = hashlib.sha256(hashlib.sha256(header).digest()).digest()
        self.assertLess(int.from_bytes(short, 'little'), 0x7fffff << (8 * 29))

    def test_asert_starts_after_block_one(self):
        with tempfile.TemporaryDirectory() as folder:
            for bad in (None, 1, 0, True, '2'):
                with self.assertRaisesRegex(ValueError, 'asertHeight'):
                    prepare.mainnet(self.spec(folder, asertHeight=bad))

    @unittest.skipUnless(CORE.exists(), 'node sources not present')
    def test_difficulty_retargets_with_asert(self):
        sources = {p: (CORE / p).read_text() for p in prepare.FILES}
        with tempfile.TemporaryDirectory() as folder:
            keys = Path(folder) / 'keys.json'
            shutil.copyfile(prepare.TESTNET['keys'], keys)
            main = prepare.overlay(sources, net=prepare.mainnet(self.spec(folder, allocationKeys=str(keys))))
        testnet, test = prepare.overlay(sources), prepare.overlay(sources, test=True)

        def block(result):
            text = result['src/kernel/chainparams.cpp']
            start = text.index('class CTensorRegParams')
            return text[start:text.index('\n};', start)]
        for result, height in ((main, 2), (testnet, prepare.TESTNET_ASERT_HEIGHT)):
            params = block(result)
            for expected in (f'consensus.DaaV2Height = {height};', f'consensus.DaaV2PhaseBHeight = {height + 144};',
                             'consensus.DaaV2AnchorBitsPhaseA = 0x207fffff;', 'consensus.DaaV2HalfLifePhaseA = 43200;',
                             'consensus.DaaV2HalfLifePhaseB = 43200;', 'consensus.nPowTargetSpacing = 10 * 60;'):
                self.assertIn(expected, params)
            self.assertNotIn('-daav2height', params)  # no node flag can move consensus
        self.assertIn('ReadDaaV2HeightArg("-daav2height")', block(test))
        for result in (testnet, test):
            self.assertIn('DaaV2PhaseCHeight() const { return std::numeric_limits<int>::max(); }',
                          result['src/consensus/params.h'])
        # Mainnet resets the target once, at the model switch (design/MODEL-SWITCH.md).
        self.assertIn('consensus.ModelSwitchHeight = 3500;', block(main))
        for expected in ('DaaV2PhaseCHeight() const { return ModelSwitchHeight; }',
                         'DAAV2_PHASE_C_TARGET_NUM{2};', 'DAAV2_PHASE_C_TARGET_DEN{1};'):
            self.assertIn(expected, main['src/consensus/params.h'])

    def test_asert_port_doubles_per_half_life(self):
        from testnet import asert_check as a
        anchor = a.set_compact(0x1d00ffff)
        self.assertEqual(a.get_compact(a.POW_LIMIT), 0x207fffff)
        self.assertEqual(a.set_compact(a.get_compact(anchor)), anchor)
        self.assertEqual(a.asert(anchor, 600 * 11, 10), anchor)  # on schedule
        self.assertEqual(a.asert(anchor, 600 * 11 - 43200, 10), anchor // 2)  # 12 h ahead
        self.assertEqual(a.asert(anchor, 600 * 11 + 43200, 10), anchor * 2)  # 12 h behind
        self.assertEqual(a.asert(a.POW_LIMIT, 10 ** 7, 1), a.POW_LIMIT)  # capped at the limit

    def test_seeds_must_be_host_names(self):
        with tempfile.TemporaryDirectory() as folder:
            for bad in (['1.2.3.4:33440'], ['x"); evil();//.com'], ['localhost']):
                with self.assertRaisesRegex(ValueError, 'seeds must be DNS host names'):
                    prepare.mainnet(self.spec(folder, seeds=bad))

    def test_genesis_hash_matches_the_running_chains(self):
        from mainnet import genesis
        # The lab and testnet hashes come from their nodes' getblockhash 0.
        self.assertEqual(genesis.genesis_hash(1791676800, 2, 'testModel@testModelCommit'),
                         'fc0ac105e8adb2401780e6fd54ec48650912c5e460d0a4ffe14ba1b83e2df359')
        self.assertEqual(genesis.genesis_hash(prepare.GENESIS_TIME, prepare.GENESIS_NONCE),
                         '2da97e8dadb7fe27645263d3e732211cb67650adbc02a016102cc7390785cdaa')
        self.assertEqual(genesis.MODEL, prepare.MODEL + '@' + prepare.REVISION)
        spec = json.loads((prepare.ROOT / 'mainnet/network.json').read_text())
        self.assertEqual(spec['genesisHash'], genesis.genesis_hash(spec['genesisTime'], spec['genesisNonce']))

    def test_mainnet_genesis_is_pinned_by_clients(self):
        spec = json.loads((prepare.ROOT / 'mainnet/network.json').read_text())
        for path in ('protocol/miner/noet_miner.py', 'protocol/noet/indexer.py', 'web/src/wallet/networks.ts'):
            if not (prepare.ROOT.parent / path).exists():  # the protocol-only export has no website
                continue
            self.assertIn(spec['genesisHash'], (prepare.ROOT.parent / path).read_text(), path)

    @unittest.skipUnless(CORE.exists(), 'node sources not present')
    def test_identity_differs_from_testnet_and_lab(self):
        with tempfile.TemporaryDirectory() as folder:
            keys = Path(folder) / 'keys.json'
            shutil.copyfile(prepare.TESTNET['keys'], keys)  # stand-in signers for the test only
            net = prepare.mainnet(self.spec(folder, genesisTime=1800000000, genesisNonce=0,
                                            allocationKeys=str(keys)))
            sources = {p: (CORE / p).read_text() for p in prepare.FILES}
            main = prepare.overlay(sources, net=net)
            test = prepare.overlay(sources)
        params = main['src/kernel/chainparams.cpp']
        for expected in ('bech32_hrp = "noet";', 'nDefaultPort = 33440;', 'pchMessageStart[1] = 0x4d;',
                         'pchMessageStart[2] = 0x4e;', 'pchMessageStart[3] = 0x31;',
                         'CreateGenesisBlock(1800000000, 0,', '(1,0x3C)', '{0x04, 0x72, 0x4d, 0x50}',
                         'vSeeds.emplace_back("noet-mainnet.fly.dev");'):
            self.assertIn(expected, params)
        self.assertNotIn('noet-mainnet.fly.dev', test['src/kernel/chainparams.cpp'])
        self.assertNotEqual(params, test['src/kernel/chainparams.cpp'])
        self.assertEqual(sorted(p for p in main if 'noet_' in p),
                         ['src/noet_mainnet.h', 'src/noet_mainnet_allocations.h', 'src/noet_mainnet_policy.h'])
        for path, text in main.items():
            self.assertNotIn('noet::testnet', text, path)
            self.assertNotIn('noet_testnet', text, path)
        self.assertIn('NOET mainnet requires its own chain', main['src/init.cpp'])
        # Allocations are shared; mainnet releases 12.5% a year, the testnet 15%.
        self.assertIn('RELEASE_BASIS_POINTS = 1250;', main['src/noet_mainnet_policy.h'])
        self.assertIn('EXPECTED_EPOCHS = 271;', main['src/noet_mainnet_policy.h'])
        self.assertIn('RELEASE_BASIS_POINTS = 1500;', test['src/noet_testnet_policy.h'])
        self.assertIn('12.5% of the remaining 80M', main['src/validation.cpp'])
        self.assertEqual(main['src/noet_mainnet_allocations.h'].replace('mainnet', 'testnet'),
                         test['src/noet_testnet_allocations.h'])
        self.assertEqual(main['src/consensus/amount.h'].replace('mainnet', 'testnet'), test['src/consensus/amount.h'])


if __name__ == '__main__':
    unittest.main()
