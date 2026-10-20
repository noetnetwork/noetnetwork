"""Private-address transport boundary for development verification services."""
import ipaddress

MAX_REQUEST_BYTES = 32 * 1024 * 1024
_PRIVATE = tuple(ipaddress.ip_network(value) for value in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def private_endpoint(host, port):
    if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Use a private IP address and valid port for verifier transport')
    if '%' in host:
        raise ValueError('Scoped addresses are not supported')
    address = ipaddress.ip_address(host)
    if not any(address.version == network.version and address in network for network in _PRIVATE):
        raise ValueError('Verifier transport requires loopback or an explicit private IPv4 address')
    return 'tcp://' + str(address) + ':' + str(port)


def check_request(message):
    if not isinstance(message, bytes) or not 8 <= len(message) <= MAX_REQUEST_BYTES:
        raise ValueError('Invalid verifier request envelope size')
