"""Development Chrome native-messaging bridge to an encrypted local lab wallet.

No browser-held keys, passphrases, arbitrary RPC or automatic broadcast.
"""
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
from urllib.parse import urlsplit
from .rpc import NodeRPC, verify_network
from .wallet_review import review_psbt
from .wallet_prompt import desktop_approval, desktop_send_approval
from .wallet_network import profile_for
from .wallet_review import amount, coin
from .wallet_prepare import prepare_transfer, transfer_intent
from .wallet_backup import backup_wallet

LAB_GENESIS = json.loads((Path(__file__).resolve().parents[1] / 'lab.network.json').read_text())['genesis']
MAX_MESSAGE = 1024 * 1024
METHODS = {'status', 'receive', 'prepare', 'review', 'sign', 'send', 'balances', 'history', 'backup'}


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        part = stream.read(count - len(data))
        if not part:
            raise ValueError('Incomplete native message')
        data.extend(part)
    return bytes(data)


def read_message(stream):
    first = stream.read(1)
    if not first:
        return None
    length = struct.unpack('=I', first + read_exact(stream, 3))[0]
    if not 1 <= length <= MAX_MESSAGE:
        raise ValueError('Invalid native message length')
    return json.loads(read_exact(stream, length), object_pairs_hook=no_duplicates)


def write_message(stream, value):
    raw = json.dumps(value, allow_nan=False).encode()
    if len(raw) > MAX_MESSAGE:
        raw = b'{"ok":false,"error":"Response exceeds the native message limit"}'
    stream.write(struct.pack('=I', len(raw)) + raw)
    stream.flush()


def load_config(path, origin):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError('Native host config must be a private regular file')
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('Native host config exceeds size limit')
    config = json.loads(raw, object_pairs_hook=no_duplicates)
    if not isinstance(config, dict) or set(config) != {'extensionId', 'rpcUrl', 'cookieFile', 'genesis'}:
        raise ValueError('Invalid native host configuration')
    if not isinstance(config['extensionId'], str) or not re.fullmatch('[a-p]{32}', config['extensionId']):
        raise ValueError('Invalid extension identity')
    if origin != 'chrome-extension://' + config['extensionId'] + '/':
        raise ValueError('Extension origin not allowed')
    url = urlsplit(config['rpcUrl'])
    if url.hostname not in ('localhost', '127.0.0.1', '::1') or not url.path.startswith('/wallet/') or not url.path[8:]:
        raise ValueError('Select a wallet on a local node')
    if not isinstance(config['cookieFile'], str) or not Path(config['cookieFile']).is_absolute():
        raise ValueError('Use an absolute local RPC cookie path')
    profile_for(config['genesis'])
    rpc = NodeRPC(config['rpcUrl'], config['cookieFile'])
    verify_network(rpc, config['genesis'])
    return config, rpc


def psbt_text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 700000:
        raise ValueError('Invalid PSBT size')
    value = value.strip()
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError('Invalid PSBT encoding') from None
    if not raw.startswith(b'psbt\xff'):
        raise ValueError('Invalid PSBT header')
    return value


def handle(request, rpc, genesis, approve=desktop_approval, approve_send=desktop_send_approval):
    if not isinstance(request, dict) or request.get('method') not in METHODS:
        raise ValueError('Unsupported native wallet request')
    method = request['method']
    expected = {'method'} if method in ('status', 'receive', 'balances', 'history', 'backup') else ({'method', 'address', 'amount'} if method == 'prepare' else {'method', 'psbt'})
    if set(request) != expected:
        raise ValueError('Unexpected native wallet request fields')
    profile = profile_for(genesis)
    def bound_review(value):
        if any(not row['address'].startswith(profile['prefix']+'1') for row in value.get('outputs', [])):
            raise ValueError('Transaction output belongs to a different network')
        return {**value, 'networkGenesis': genesis, 'networkName': profile['name']}
    if method == 'prepare':
        transfer_intent(request['address'], request['amount'])
        if not request['address'].startswith(profile['prefix']+'1'):
            raise ValueError('Recipient belongs to a different network')
    verify_network(rpc, genesis)
    info = rpc('getwalletinfo')
    if 'unlocked_until' not in info or info.get('private_keys_enabled') is False:
        raise ValueError('Select an encrypted native signing wallet')
    if method == 'status':
        return {'network': profile['name'], 'genesis': genesis, 'addressPrefix': profile['prefix'], 'testCoinsOnly': True, 'locked': info['unlocked_until'] == 0, 'signingSupported': True, 'preparationSupported': True, 'broadcastSupported': True, 'balancesSupported': True, 'historySupported': True, 'backupSupported': True}
    if method == 'balances':
        mine = rpc('getbalances')['mine']
        return {'trusted': coin(amount(mine['trusted'])), 'pending': coin(amount(mine['untrusted_pending'])),
                'immature': coin(amount(mine['immature'])), 'testCoinsOnly': True, 'genesis': genesis}
    if method == 'backup':
        return backup_wallet(rpc, genesis, profile['name'])
    if method == 'history':
        rows = rpc('listtransactions', '*', 25, 0, False)
        if not isinstance(rows, list) or len(rows) > 25:
            raise ValueError('Invalid wallet history')
        result = []
        for row in reversed(rows):
            if not isinstance(row, dict) or not re.fullmatch(r'[a-f0-9]{64}', row.get('txid', '')):
                raise ValueError('Invalid wallet transaction')
            from decimal import Decimal
            value = Decimal(str(row['amount']))
            # listtransactions uses signed amounts, unlike unsigned UTXO values.
            formatted = ('-' if value < 0 else '') + coin(amount(abs(value)))
            result.append({'txid': row['txid'], 'category': row['category'], 'amount': formatted,
                'confirmations': row['confirmations'], 'time': row['time'], 'address': row.get('address')})
        return {'transactions': result, 'limit': 25, 'testCoinsOnly': True, 'genesis': genesis}
    if method == 'receive':
        address = rpc('getnewaddress', '', 'bech32')
        if not isinstance(address, str) or not address.startswith(profile['prefix']+'1'):
            raise ValueError('Unexpected native address')
        return {'address': address, 'testCoinsOnly': True}
    if method == 'prepare':
        prepared = prepare_transfer(rpc, request['address'], request['amount'])
        return {**prepared, 'review': bound_review(prepared['review'])}
    psbt = psbt_text(request['psbt'])
    review = bound_review(review_psbt(rpc, psbt))  # Fixed fee limits, no browser override.
    if method == 'review':
        return review
    if info['unlocked_until'] != 0:
        raise ValueError('Lock the native wallet before requesting a signature')
    password = approve_send(review) if method == 'send' else approve(review)
    if password is None:
        return {'cancelled': True, 'broadcast': False}
    # Approval may take time. Recheck chain and UTXOs before unlocking.
    verify_network(rpc, genesis)
    if bound_review(review_psbt(rpc, psbt)) != review:
        raise ValueError('Transaction review changed; request a fresh review')
    if rpc('getwalletinfo').get('unlocked_until') != 0:
        raise ValueError('Wallet lock changed; request a fresh review')
    try:
        rpc('walletpassphrase', password, 30)
        signed = rpc('walletprocesspsbt', psbt, True, 'ALL')
        if not signed.get('complete'):
            raise ValueError('Native wallet could not fully sign')
        result = psbt_text(signed['psbt'])
    finally:
        password = None
        rpc('walletlock')
    final = rpc('finalizepsbt', result)
    if not final.get('complete'):
        raise ValueError('Signed transaction could not be finalized')
    tx = rpc('decoderawtransaction', final['hex'])
    if method == 'send':
        verify_network(rpc, genesis)
        acceptance = rpc('testmempoolaccept', [final['hex']])
        if not isinstance(acceptance, list) or len(acceptance) != 1 or acceptance[0].get('allowed') is not True or acceptance[0].get('txid') != tx['txid']:
            raise ValueError('The node refused the signed payment. Nothing was broadcast.')
        txid = rpc('sendrawtransaction', final['hex'], '0.001')
        if txid != tx['txid']:
            raise RuntimeError('Broadcast outcome is uncertain. Check wallet history before retrying.')
        return {'txid': txid, 'broadcast': True, 'testCoinsOnly': True, 'genesis': genesis}
    return {'psbt': result, 'txid': tx['txid'], 'broadcast': False, 'testCoinsOnly': True}


def main():
    # The generated launcher supplies the fixed config path, then Chrome supplies
    # its authenticated extension origin. Extra command arguments are refused.
    try:
        if len(sys.argv) != 3:
            raise ValueError('Native host expects configuration and extension origin')
        config, rpc = load_config(sys.argv[1], sys.argv[2])
        fd = os.open(sys.argv[1] + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'r+b') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError('Native host lock must be a private regular file')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            request = read_message(sys.stdin.buffer)
            if request is None:
                return
            # One message per process. No persistent unlocked or approval session.
            result = handle(request, rpc, config['genesis'])
            write_message(sys.stdout.buffer, {'ok': True, 'result': result})
    except Exception:
        # RPC responses, paths and passwords must never enter Chrome error logs.
        write_message(sys.stdout.buffer, {'ok': False, 'error': 'Native wallet request failed. Check the local node, wallet lock and reviewed host setup.'})


if __name__ == '__main__':
    main()
