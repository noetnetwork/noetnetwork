import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from urllib.request import ProxyHandler
from noet.rpc import NodeRPC,RPCError

class PrivateRPCTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.cookie=Path(folder.name)/'cookie';self.cookie.write_text('__cookie__:secret');self.cookie.chmod(0o600)
        self.rpc=NodeRPC('http://127.0.0.1:29443',self.cookie)
        self.rpc.opener=Mock()
    def response(self,data):
        self.rpc.opener.open.return_value=io.BytesIO(json.dumps(data).encode())
    def test_cookie_rotates_and_is_only_sent_in_authorization(self):
        for secret in ('first','second'):
            self.cookie.write_text('__cookie__:'+secret)
            self.response({'id':'noet','result':42})
            self.assertEqual(self.rpc('getblockcount'),42)
            request=self.rpc.opener.open.call_args.args[0]
            import base64
            self.assertEqual(request.get_header('Authorization'),'Basic '+base64.b64encode(('__cookie__:'+secret).encode()).decode())
            self.assertNotIn(secret,request.full_url);self.assertNotIn(secret,request.data.decode())
    def test_environment_proxies_are_disabled(self):
        with patch.dict(os.environ,{'http_proxy':'http://example.invalid:1234','https_proxy':'http://example.invalid:1234'}):
            rpc=NodeRPC('https://localhost:29443',self.cookie)
            self.assertFalse(any(isinstance(handler,ProxyHandler) and handler.proxies for handler in rpc.opener.handlers))
    def test_cookie_bounds_permissions_and_special_files(self):
        for text in ('a:',':b','missing separator','a:b\nc:d','a:'+'x'*4096,'a:\x00b'):
            self.cookie.write_text(text)
            with self.assertRaises(RPCError):self.rpc('status')
        self.cookie.write_text('a:b');self.cookie.chmod(0o644)
        with self.assertRaises(RPCError):self.rpc('status')
        self.cookie.unlink();self.cookie.symlink_to('/etc/passwd')
        with self.assertRaises(RPCError) as error:self.rpc('status')
        self.assertNotIn('/etc/passwd',str(error.exception))
        self.cookie.unlink();os.mkfifo(self.cookie,0o600)
        with self.assertRaises(RPCError):self.rpc('status')
        self.rpc.opener.open.assert_not_called()
    def test_response_identity_and_errors_are_checked(self):
        for data in ([],{}, {'id':'other','result':1}, {'id':'noet','result':None,'error':'secret server error'}):
            self.response(data)
            with self.assertRaises(RPCError) as error:self.rpc('status')
            self.assertNotIn('secret',str(error.exception))
    def test_rejects_unsafe_urls(self):
        for url in ('http://example.com','https:///missing','https://user:pass@localhost','https://localhost?token=x','http://localhost:0'):
            with self.assertRaises(ValueError):NodeRPC(url,self.cookie)
