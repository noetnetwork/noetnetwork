#!/usr/bin/env python3
"""Real native CLTV acceptance on disposable, isolated laboratory test coins.

This tests a script primitive, not a deployed allocation schedule or useful work.
The publicly known private scalars below are test fixtures, never beneficiaries.
"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from node_smoke import cmd, rpc, wait_for, IMAGE, DOCKER, prefix

ROOT = Path(__file__).resolve().parents[1]
LOCK_HEIGHT = 110
VALUE = 100_000_000
FEE = 2_000


def main():
    if not __debug__:
        raise RuntimeError('Acceptance requires assertions; do not use Python -O')
    if sys.version_info < (3, 12):
        raise RuntimeError('Use Python 3.12 or newer for filtered source extraction')
    lock = json.loads((ROOT / 'upstream.lock.json').read_text())
    source = ROOT / '.upstream/services/core-node/bcore'
    revision = lock['components']['services/core-node/bcore']
    # Import exact committed test serializers, not a potentially modified checkout.
    archive = subprocess.check_output(['git', '-C', str(source), 'archive', revision,
                                     'test/functional/test_framework'], timeout=30)
    with tempfile.TemporaryDirectory(prefix='noet-vesting-framework-') as folder:
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(folder, filter='data')
        sys.path.insert(0, str(Path(folder) / 'test/functional'))
        from test_framework.key import ECKey
        from test_framework.messages import CTransaction, CTxIn, CTxOut, COutPoint, CTxInWitness
        from test_framework.script import CScript, OP_CHECKLOCKTIMEVERIFY, OP_DROP, OP_CHECKMULTISIG, SegwitV0SignatureHash, SIGHASH_ALL

        keys = [ECKey() for _ in range(4)]
        for scalar, key in enumerate(keys, 1):
            key.set(scalar.to_bytes(32, 'big'), compressed=True)
        custody = [2, *(key.get_pubkey().get_bytes() for key in keys[:3]), 3, OP_CHECKMULTISIG]
        script = CScript([LOCK_HEIGHT, OP_CHECKLOCKTIMEVERIFY, OP_DROP, *custody])
        image_id = cmd('image', 'inspect', '--format', '{{.Id}}', IMAGE)
        node = prefix + '-vesting'
        created = False
        cmd('network', 'create', '--internal', prefix)
        try:
            cmd('run', '-d', '--name', node, '--network', prefix, '--pull=never', image_id,
                '-server=1', '-txindex=1', '-validationapi=mock', '-useextapi=0',
                '-listen=0', '-dnsseed=0', '-connect=0', '-rpcport=29443',
                '-fallbackfee=0.0002')
            created = True
            wait_for(lambda: rpc(node, 'getblockchaininfo'))
            genesis = rpc(node, 'getblockhash', 0)
            assert genesis == json.loads((ROOT / 'lab.network.json').read_text())['genesis']
            rpc(node, 'createwallet', 'vesting-disposable')
            destination = rpc(node, 'getnewaddress', '', 'bech32')
            output_script = bytes.fromhex(rpc(node, 'getaddressinfo', destination)['scriptPubKey'])
            decoded_script = rpc(node, 'decodescript', script.hex())
            address = decoded_script['segwit']['address']
            expected_script = '0020' + hashlib.sha256(script).hexdigest()
            assert rpc(node, 'validateaddress', address)['scriptPubKey'] == expected_script
            rpc(node, 'generatetoaddress', 101, destination)
            funding_id = rpc(node, 'sendtoaddress', address, '1.00000000')
            rpc(node, 'generatetoaddress', 1, destination)
            funded = rpc(node, 'getrawtransaction', funding_id, True)
            outputs = [row for row in funded['vout'] if row['scriptPubKey']['hex'] == expected_script]
            assert len(outputs) == 1 and outputs[0]['value'] == '1.00000000'
            outpoint = outputs[0]['n']

            def spend(locktime=LOCK_HEIGHT, sequence=0xfffffffe, signers=(0, 1), witness_script=script):
                tx = CTransaction()
                tx.version = 2
                tx.nLockTime = locktime
                tx.vin = [CTxIn(COutPoint(int(funding_id, 16), outpoint), nSequence=sequence)]
                tx.vout = [CTxOut(VALUE - FEE, output_script)]
                witness = CTxInWitness()
                digest = SegwitV0SignatureHash(witness_script, tx, 0, SIGHASH_ALL, VALUE)
                signatures = [keys[index].sign_ecdsa(digest, rfc6979=True) + bytes([SIGHASH_ALL]) for index in signers]
                witness.scriptWitness.stack = [b'', *signatures, bytes(witness_script)]
                tx.wit.vtxinwit = [witness]
                return tx.serialize().hex()

            checks = {}
            def rejected(name, raw, reason):
                result = rpc(node, 'testmempoolaccept', [raw], '0.001')[0]
                assert result['allowed'] is False, (name, result)
                assert reason.lower() in result['reject-reason'].lower(), (name, result)
                checks[name] = result['reject-reason']

            valid = spend()
            rejected('before_lock_height', valid, 'non-final')
            rpc(node, 'generatetoaddress', LOCK_HEIGHT - 1 - rpc(node, 'getblockcount'), destination)
            rejected('one_block_before_admission', valid, 'non-final')
            rpc(node, 'generatetoaddress', 1, destination)
            assert rpc(node, 'getblockcount') == LOCK_HEIGHT
            assert rpc(node, 'testmempoolaccept', [valid], '0.001')[0]['allowed'] is True
            rejected('lower_transaction_locktime', spend(locktime=0), 'Locktime requirement not satisfied')
            rejected('final_input_sequence', spend(sequence=0xffffffff), 'Locktime requirement not satisfied')
            rejected('one_signature_only', spend(signers=(0,)), 'script')
            rejected('no_signatures', spend(signers=()), 'script')
            rejected('wrong_beneficiary', spend(signers=(0, 3)), 'script')
            rejected('duplicate_signer', spend(signers=(0, 0)), 'script')
            rejected('reversed_signatures', spend(signers=(1, 0)), 'script')
            for signers in ((0, 2), (1, 2)):
                assert rpc(node, 'testmempoolaccept', [spend(signers=signers)], '0.001')[0]['allowed'] is True
            stripped = CScript(custody)
            rejected('removed_script_lock', spend(witness_script=stripped), 'Witness program hash mismatch')
            assert rpc(node, 'getmempoolinfo')['size'] == 0
            tip = rpc(node, 'getbestblockhash')
            rpc(node, 'invalidateblock', tip)
            rejected('reorg_before_admission', valid, 'non-final')
            rpc(node, 'reconsiderblock', tip)
            assert rpc(node, 'testmempoolaccept', [valid], '0.001')[0]['allowed'] is True
            txid = rpc(node, 'sendrawtransaction', valid, '0.001')
            block = rpc(node, 'generatetoaddress', 1, destination)[0]
            assert txid in rpc(node, 'getblock', block)['tx']
            # The CLI renders a null RPC result as empty stdout.
            assert rpc(node, 'gettxout', funding_id, outpoint) == ''
            assert rpc(node, 'gettxout', txid, 0)['value'] == '0.99998000'
            cmd('restart', node)
            wait_for(lambda: rpc(node, 'getbestblockhash') == block)
            assert rpc(node, 'gettxout', funding_id, outpoint) == ''
            assert rpc(node, 'gettxout', txid, 0)['value'] == '0.99998000'
            cmd('exec', node, 'python3', '-c',
                "from pathlib import Path; Path('/home/noet/.tensorcash/bitcoin.conf').write_text('reindex=1\\n')")
            cmd('restart', node)
            wait_for(lambda: rpc(node, 'getbestblockhash') == block, seconds=120)
            assert rpc(node, 'gettxout', funding_id, outpoint) == ''
            assert rpc(node, 'gettxout', txid, 0)['value'] == '0.99998000'
            print(json.dumps({'status': 'passed', 'imageId': image_id, 'sourceCommit': revision,
                'genesis': genesis, 'network': 'NOET laboratory', 'testCoinsOnly': True,
                'locktime': LOCK_HEIGHT, 'firstEligibleBlock': LOCK_HEIGHT + 1,
                'rejections': checks, 'boundaryAccepted': True, 'validSignerPairs': 3, 'reorgChecked': True,
                'restartChecked': True, 'reindexChecked': True, 'witnessScript': script.hex(),
                'scope': 'CLTV plus 2-of-3 P2WSH script primitive; no production allocations, cancellation rule, consensus upgrade or useful-work proof'}, indent=2))
        finally:
            if created:
                subprocess.run(DOCKER + ['rm', '-f', node], capture_output=True, timeout=30)
            subprocess.run(DOCKER + ['network', 'rm', prefix], capture_output=True, timeout=30)


if __name__ == '__main__':
    main()
