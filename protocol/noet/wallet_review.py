"""Conservative review of ordinary native laboratory transfers before unlocking.

Not a general-purpose signer: custom scripts/assets, external inputs, finalized
inputs and non-ALL signature modes require a separately reviewed implementation.
"""
import hashlib
import re
from .indexer import atoms

MAX_ATOMS = 21_000_000 * 100_000_000  # current laboratory monetary range


def amount(value):
    result = atoms(value)
    if result > MAX_ATOMS:
        raise ValueError('Amount exceeds the laboratory monetary range')
    return result


def coin(value):
    return f'{value // 100_000_000}.{value % 100_000_000:08d}'


def standard_output(output, rpc, value_key):
    value = amount(output[value_key])
    script = output['scriptPubKey']
    # Restrict this release to ordinary native P2WPKH, matching receive().
    if script.get('type') != 'witness_v0_keyhash' or not re.fullmatch(r'0014[0-9a-f]{40}', script.get('hex', '')):
        raise ValueError('Only ordinary native witness key-hash transfers can be reviewed')
    address = script.get('address', '')
    if not re.fullmatch(r'(?:nlab|nwrk)1q[023456789acdefghjklmnpqrstuvwxyz]{38}', address):
        raise ValueError('Wrong-network output')
    info = rpc('getaddressinfo', address)
    if info.get('scriptPubKey') != script['hex']:
        raise ValueError('Address does not match its output script')
    return {'address': address, 'atoms': value, 'script': script['hex'],
            'owned': info.get('ismine') is True and info.get('iswatchonly') is not True}


def review_psbt(rpc, psbt, max_fee='0.001'):
    limit = amount(max_fee)
    if limit <= 0:
        raise ValueError('A positive maximum fee is required')
    decoded = rpc('decodepsbt', psbt)
    tx = decoded['tx']
    if tx.get('version') not in (1, 2) or decoded.get('psbt_version', 0) != 0:
        raise ValueError('Unsupported transaction or PSBT version')
    if decoded.get('unknown') or decoded.get('proprietary'):
        raise ValueError('Unknown PSBT extensions require separate review')
    inputs, outputs = decoded['inputs'], tx['vout']
    if not inputs or not outputs or len(inputs) > 100 or len(outputs) > 100 or len(inputs) != len(tx['vin']):
        raise ValueError('Unsupported input/output count')
    total_in = 0
    seen = set()
    for vin, data in zip(tx['vin'], inputs):
        if data.get('sighash', 'ALL') != 'ALL':
            raise ValueError('Only SIGHASH_ALL is supported')
        if any(data.get(k) for k in ('unknown', 'proprietary', 'final_scriptSig', 'final_scriptwitness', 'partial_signatures')):
            raise ValueError('Extended or finalized inputs cannot be reviewed for signing')
        point = (vin['txid'], vin['vout'])
        if not re.fullmatch(r'[0-9a-f]{64}', point[0]) or type(point[1]) is not int or point[1] < 0 or point in seen:
            raise ValueError('Invalid or duplicate input')
        seen.add(point)
        if 'witness_utxo' not in data:
            raise ValueError('Every input must include witness UTXO data')
        declared = standard_output(data['witness_utxo'], rpc, 'amount')
        actual = rpc('gettxout', point[0], point[1], True)
        if not actual:
            raise ValueError('Input is spent, missing or not available at the connected node')
        live = standard_output(actual, rpc, 'value')
        if declared != live or not live['owned']:
            raise ValueError('Input must match an unspent output owned by this wallet')
        if actual.get('coinbase') and actual.get('confirmations', 0) < 100:
            raise ValueError('Mining reward is not mature')
        total_in += live['atoms']
    reviewed = [standard_output(output, rpc, 'value') for output in outputs]
    if any(item.get('unknown') or item.get('proprietary') for item in decoded.get('outputs', [])):
        raise ValueError('Unknown output metadata requires separate review')
    total_out = sum(row['atoms'] for row in reviewed)
    fee = total_in - total_out
    if fee < 0 or fee > limit or amount(decoded['fee']) != fee:
        raise ValueError('Fee is inconsistent or exceeds the chosen maximum')
    analysis = rpc('analyzepsbt', psbt)
    size = analysis.get('estimated_vsize')
    if analysis.get('error') or type(size) is not int or size <= 0 or fee > size * 100:
        raise ValueError('Fee rate unavailable or above 100 atoms per virtual byte')
    return {'network': 'NOET laboratory; test coins only',
            'psbtFingerprint': hashlib.sha256(psbt.encode('ascii')).hexdigest(),
            'inputs': len(inputs), 'locktime': tx['locktime'],
            'replaceable': any(v.get('sequence', 0xffffffff) < 0xfffffffe for v in tx['vin']),
            'outputs': [{'address':row['address'], 'testNOET':coin(row['atoms']),
                         'ownership':'This wallet (verify intended change)' if row['owned'] else 'External recipient'} for row in reviewed],
            'feeTestNOET':coin(fee), 'estimatedVsize':size,
            'feeRateAtomsPerVbyte':f'{fee}/{size}', 'broadcast':False}
