#!/usr/bin/env python3
"""Exercise real ZMQ framing/reconnection over the mutual-TLS relay on loopback.

Requires pyzmq==27.1.0 in a disposable Python environment. No GPU or node is used.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest

import zmq
import zmq.asyncio
from test_secure_transport import SecureTransportTests


class ZmqTransportTests(SecureTransportTests):
    async def test_multipart_frames_and_reconnect(self):
        context = zmq.asyncio.Context()
        pull = context.socket(zmq.PULL); push = context.socket(zmq.PUSH)
        for sock in (pull, push):
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.SNDHWM, 4); sock.setsockopt(zmq.RCVHWM, 4)
        from dataclasses import replace
        target_port = pull.bind_to_random_port('tcp://127.0.0.1')
        server = await self.relay(replace(self.server_config, target_port=target_port))
        client = await self.relay(self.client_config)
        push.setsockopt(zmq.RECONNECT_IVL, 20)
        push.connect('tcp://127.0.0.1:'+str(self.client_config.listen_port))
        try:
            payloads = [[b'\x00NOET\xff', bytes(range(256))*8192, b'', b'signed-envelope'], [b'after-reconnect', b'\x00'*32]]
            await asyncio.wait_for(push.send_multipart(payloads[0]), 5)
            self.assertEqual(await asyncio.wait_for(pull.recv_multipart(), 5), payloads[0])
            await client.close()
            client = await self.relay(self.client_config)
            await asyncio.wait_for(push.send_multipart(payloads[1]), 5)
            self.assertEqual(await asyncio.wait_for(pull.recv_multipart(), 5), payloads[1])
            self.assertEqual(client.accepted, 1)
            self.assertGreaterEqual(server.accepted, 2)
            global ACCEPTANCE
            ACCEPTANCE = {'platform':platform.system(), 'architecture':platform.machine(),
                'python':platform.python_version(), 'pyzmq':zmq.__version__, 'libzmq':zmq.zmq_version(),
                'multipartMessages':len(payloads), 'frames':sum(map(len,payloads)),
                'payloadBytes':sum(len(frame) for message in payloads for frame in message),
                'payloadSha256':hashlib.sha256(b''.join(frame for message in payloads for frame in message)).hexdigest(),
                'reconnectPassed':True, 'scope':'Real ZeroMQ framing over loopback mutual TLS; no node, GPU or multi-host acceptance',
                'productionReady':False}
        finally:
            push.close(); pull.close(); context.term()


if __name__ == '__main__':
    if zmq.__version__ != '27.1.0':
        raise SystemExit('Use the pinned acceptance dependency pyzmq==27.1.0')
    suite = unittest.TestSuite([ZmqTransportTests('test_multipart_frames_and_reconnect')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    print(json.dumps(ACCEPTANCE, indent=2))
