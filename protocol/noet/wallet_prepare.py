"""Prepare one ordinary laboratory payment without unlocking or signing."""
import re
from .wallet_review import amount, coin, review_psbt


def transfer_intent(address, value):
    if not isinstance(address, str) or address != address.lower() or not re.fullmatch(r'(?:nlab|nwrk)1q[023456789acdefghjklmnpqrstuvwxyz]{38}', address):
        raise ValueError('Use a native laboratory witness key-hash address')
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]{0,7})(\.[0-9]{1,8})?', value):
        raise ValueError('Use a positive decimal amount with at most eight decimal places')
    atoms = amount(value)
    if atoms <= 0:
        raise ValueError('The transfer amount must be positive')
    return address, coin(atoms)


def prepare_transfer(rpc, address, value):
    address, value = transfer_intent(address, value)
    destination = rpc('validateaddress', address)
    if not isinstance(destination, dict) or destination.get('isvalid') is not True or not re.fullmatch(r'0014[0-9a-f]{40}', destination.get('scriptPubKey', '')):
        raise ValueError('The connected node did not validate the recipient')
    # Conservative fixed laboratory fee. Never accept browser-provided RPC
    # options, change addresses, subtract-fee flags or arbitrary input lists.
    # Public key-derivation metadata is required for the node to estimate
    # unsigned witness size. The PSBT remains local; it contains no private key.
    funded = rpc('walletcreatefundedpsbt', [], [{address:value}], 0,
        {'fee_rate':2, 'replaceable':True, 'change_type':'bech32', 'lock_unspents':False, 'include_watching':False}, True)
    if not isinstance(funded, dict) or not isinstance(funded.get('psbt'), str) or len(funded['psbt']) > 700000:
        raise ValueError('The node returned an invalid transaction draft')
    review = review_psbt(rpc, funded['psbt'])
    requested = [row for row in review['outputs'] if row['address'] == address and row['testNOET'] == value]
    if len(requested) != 1:
        raise ValueError('Prepared transaction does not match the requested payment')
    for row in review['outputs']:
        if row is not requested[0] and row['ownership'] != 'This wallet (verify intended change)':
            raise ValueError('Prepared transaction includes an unexpected external recipient')
    return {'psbt':funded['psbt'], 'review':review, 'broadcast':False, 'testCoinsOnly':True}
