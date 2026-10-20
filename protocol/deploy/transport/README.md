# Private node and verifier transport

This development relay wraps the native ZeroMQ byte stream in mutual TLS 1.3. Both sides verify the dedicated CA chain and exact peer certificate SHA-256. The client also verifies the server's DNS identity. The relay opens a server-side plaintext target only after authenticating the client, and forwards client-side payloads only after authenticating the server. There is no unencrypted fallback, public listener, DNS-based routing or HTTP proxy support.

The existing verdict HMAC remains required. TLS protects the connection in both directions; HMAC binds a verdict to its request identity, type and ruleset. Neither establishes that an AI proof is correct.

## Connections

The example private IPs are illustrative, not deployed hosts. Use a private routed network between machines. Keep all native application sockets on loopback in the same network namespace as their relays.

| Direction | Local application connection | Encrypted link | Destination application |
| --- | --- | --- | --- |
| Requests | Node to `127.0.0.1:16001` | Node relay to verifier `10.42.0.20:16443` | Verifier relay to `127.0.0.1:6001` |
| Verdicts | Verifier to `127.0.0.1:17001` | Verifier relay to node `10.42.0.10:17443` | Node relay to `127.0.0.1:7001` |

The four example JSON files each describe one relay process. Replace both private addresses, certificate paths, server identities and zero fingerprint placeholders. Zero pins intentionally cannot authenticate a real peer. Copy the completed configurations to private files with mode 0600; example files are not runnable deployment configurations.

For the native node, set `VALIDATOR_HOST=127.0.0.1`, `VALIDATOR_PUSH_PORT=16001`, `VALIDATOR_PULL_BIND=127.0.0.1`, and `VALIDATOR_PULL_PORT=7001`. For the guarded verifier, set `NOET_VERIFIER_BIND=127.0.0.1`, `ZMQ_VERIFY_PULL_PORT=6001`, `ZMQ_VERIFY_PUSH_HOST=127.0.0.1`, and `ZMQ_VERIFY_PUSH_PORT=17001`. Supply the same independently generated 32-byte hex `VALIDATOR_SHARED_SECRET` to the node and verifier through their private environment/secret store. Do not put it in the JSON examples, shell history or repository.

The guarded verifier refuses non-loopback plaintext endpoints. The native node's configuration must separately enforce the loopback bindings above. A relay does not prevent an operator from exposing another service or starting an unguarded binary.

## Certificates and operation

Use a dedicated private CA with its signing key kept off worker machines. Issue separate short-lived certificates and keys for the four relay identities. Request clients need `clientAuth`; request servers need `serverAuth` and their exact DNS subject alternative name. Apply the same separation to verdict clients and servers. Each configuration pins the opposite endpoint's leaf DER certificate SHA-256, not the PEM file hash. Approve and exchange pins over a trusted administrative channel.

Only CA/certificate public material and that relay's private key belong on its host. Keys must be regular files with mode 0600 or 0400 and readable by the service user. Group/world-writable CA and certificate files, final symlinks, special files and files over 64 KiB are refused. Runtime configuration and keys must remain in operator-controlled directories. Encrypted key files are not interactively prompted for; use a secret-store-mounted service key. TLS session-key logging from the environment is deliberately not enabled.

On a POSIX host with Python 3.12 or newer, with OpenSSL supporting TLS 1.3, run each relay under a dedicated service supervisor:

```sh
PYTHONPATH=protocol python3 -m noet.secure_transport --config /run/noet-tls/node-request.json
```

Use the corresponding configuration for the other three processes. Restart the service supervisor after reviewing code/runtime updates. Limit each process's memory and file descriptors through the host/container supervisor, restrict TLS listener access to the paired host, and keep certificate/configuration mounts read-only. Do not publish native RPC or raw ZeroMQ ports. No service, certificate authority or hosted machine is created by this guide.

Each relay allows four admitted connections in total, including incomplete TLS handshakes and active forwarded connections, five-second handshakes/connects, 120 seconds without traffic in either direction, and at most one hour per authenticated session, shortened to the peer certificate's expiry. ZMQ reconnects and authenticates again. There are 64 KiB stream buffers with write backpressure; these are not a total process-memory guarantee. Admission happens immediately after TCP accept and before creating a stream or TLS protocol. Excess sockets close without beginning TLS. The listener backlog is also four. Connection limits do not bound connection churn, kernel queues or the entire Python/OpenSSL process, so peer firewall restrictions and process resource limits remain necessary. An EOF or failure closes both directions; TCP half-close is not preserved because these are persistent ZMQ links.

Rotation requires new certificates, approved pins and a restart of the paired processes. Restart all affected relays for immediate revocation of an established connection. There is no online CRL/OCSP integration. A replaced configuration is not hot-reloaded. Same-user local processes can reach a loopback listener; the operating system remains part of the trust boundary.

## Acceptance and limits

The protocol suite tests actual TLS sockets with temporary certificates: valid binary transfer, missing/untrusted/wrong-purpose clients, wrong pins, wrong server names, modified certificate signatures, TLS-version refusal, capacity handling, idle/session expiry and shutdown cleanup. Certificates and test keys are temporary and never committed.

Eighteen TLS cases pass on macOS and Linux Python 3.12, including a stalled-handshake saturation test, rejection before TLS allocation, recovery after an admission slot is freed, malformed-handshake cleanup and shutdown during a handshake. See `protocol/validation/relay-admission.json`.

The additional `protocol/tests/secure_zmq_smoke.py` check requires the pinned `pyzmq==27.1.0` dependency in a disposable environment. It sends actual multipart ZMQ frames through both relays, restarts the client relay, and verifies delivery after reconnection. It does not run GPU verification or establish cross-host/cloud acceptance. The older verification service dependency remains separately pinned; this test does not certify its complete runtime.

Production deployment still needs accepted node/verifier images, certificate issuance and rotation operations, private routing/firewalls, monitored resource limits, multi-host acceptance and independent review. This work does not enable public mining.

Reference: [Python SSL documentation](https://docs.python.org/3.12/library/ssl.html) and [asyncio stream documentation](https://docs.python.org/3.12/library/asyncio-stream.html).

## CPU image packaging

`protocol/prepare_transport.py` creates a deterministic, allowlisted build context from a reviewed Python 3.12+ parent image specified by its immutable digest. It includes three Python modules and their hashes, runs as user 1000, and contains no credentials or deployment configuration. It rejects mutable image tags and existing output paths. It does not build, publish or sign an image.

```sh
python3 protocol/prepare_transport.py --base-image "$REVIEWED_PYTHON_IMAGE" --out /tmp/noet-transport-context.tar
```

A deployed relay container must share the corresponding native service's network namespace so its plaintext endpoint stays on loopback. Bind the TLS listener only to that namespace's specific private address. Mount the completed config and that role's certificates/keys read-only, readable only by its service user. Keep the container root read-only with a small private writable `/tmp` mount for transient certificate loading; do not copy secrets into image layers.
