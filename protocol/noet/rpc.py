"""Private, bounded node RPC. Never expose this client as a public RPC proxy."""
import base64
import ipaddress
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

class RPCError(RuntimeError):
    pass

class RPCRejected(RPCError):
    """The node answered and refused the call, so it had no effect."""

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class NodeRPC:
    def __init__(self, url, cookie_file):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname or parsed.port == 0:
            raise ValueError("RPC requires an HTTP(S) URL without embedded credentials or query.")
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if parsed.scheme != "https" and not loopback:
            raise ValueError("Remote RPC requires HTTPS. Use a loopback SSH tunnel for private nodes.")
        self.url, self.cookie_file = url, Path(cookie_file)
        # RPC cookies must never follow environment/system proxy configuration.
        self.opener = build_opener(ProxyHandler({}), NoRedirect)

    @classmethod
    def from_env(cls):
        return cls(os.environ["NOET_RPC_URL"], os.environ["NOET_RPC_COOKIE"])

    def __call__(self, method, *params):
        try:
            # Read each call because the node rotates cookies on restart. Reject
            # links/special files and bound reads before constructing credentials.
            fd = os.open(self.cookie_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise RPCError("RPC cookie must be a private regular file")
                raw_cookie = stream.read(4097)
            if len(raw_cookie) > 4096:
                raise RPCError("Invalid RPC cookie")
            cookie = raw_cookie.decode('ascii').strip()
            username, separator, password = cookie.partition(":")
            if not separator or not username or not password or any(ord(c) < 33 or ord(c) > 126 for c in cookie):
                raise RPCError("Invalid RPC cookie")
            req = Request(self.url, data=json.dumps({"jsonrpc": "2.0", "id": "noet", "method": method, "params": params}, allow_nan=False).encode(), headers={
                "Content-Type": "application/json",
                "Authorization": "Basic " + base64.b64encode(cookie.encode()).decode(),
            })
            with self.opener.open(req, timeout=30) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise RPCError("Node response exceeds the configured limit")
            data = json.loads(raw, parse_float=str)
            if not isinstance(data, dict) or data.get("id") != "noet" or "result" not in data:
                raise RPCError("Invalid node RPC response")
            if data.get("error"):
                raise RPCRejected("Node rejected RPC: " + method)
            return data["result"]
        except RPCError:
            raise
        except Exception:
            # Do not leak URL, credentials, server errors or wallet data.
            raise RPCError("Node RPC unavailable: " + method) from None

def verify_network(rpc, expected_genesis):
    if len(expected_genesis) != 64 or any(c not in "0123456789abcdef" for c in expected_genesis):
        raise ValueError("A pinned NOET genesis hash is required")
    if rpc("getblockhash", 0) != expected_genesis:
        raise RPCError("Wrong network: genesis does not match NOET configuration")
    info = rpc("getblockchaininfo")
    if info["chain"] != "tensor-reg":
        raise RPCError("Only the isolated NOET laboratory chain is supported by this release")
    return info
