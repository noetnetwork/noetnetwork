# NOET native wallet development build

This builds the pinned native Qt wallet with NOET presentation and an isolated lab launcher. It is **not a production wallet release**. It uses mock laboratory validation and no-value `nlab1` coins. Never import real funds or production keys.

The launcher disables seed discovery, inbound listening, RPC serving and external mining APIs. It refuses extra command-line arguments and requires a private data directory. Peers can be configured explicitly in `~/.noet-wallet-lab/noet.conf`. This file is trusted operator configuration; do not import an unknown configuration file. No keys are sent to the website or Privy.

## Build

From the repository root, after reading the upstream licenses:

```sh
python3 protocol/bootstrap.py
python3 protocol/source_bundle.py --out /tmp/noet-node-context.tar
docker build --target build -t noet-core:build - < /tmp/noet-node-context.tar
docker build -f protocol/docker/Wallet.Dockerfile -t noet-wallet:candidate protocol
docker run --rm --network none -e QT_QPA_PLATFORM=offscreen \
  --entrypoint timeout noet-wallet:candidate --signal=KILL 120s test_bitcoin-qt
```

The image is a Linux build/test artifact, not a macOS app. A desktop display connection is deliberately not exposed by these commands. Do not mount your host display socket into an untrusted container. The binary requires the Qt/runtime libraries specified in the Dockerfile. Native installers, operating-system signing, automatic updates and externally reviewed recovery flows are still release gates.

Use the upstream Qt wallet’s encrypted descriptor-wallet creation, receive, fee-review, send and backup flows on disposable lab data. The separate `noet.wallet` CLI provides explicit prepare/inspect/sign/broadcast steps. The node integration test covers encrypted backup restoration, a confirmed transfer and reindexing; that evidence does not replace manual Qt flow acceptance or external wallet review.

## Wallet patch

`patches/noet-wallet.patch` is applied only to the GUI build, after the pinned node sources. It corrects right-click transaction selection and updates stale test fixtures. Regression tests exercise replacement of an unrelated selection before checking fee-bump behavior. Laboratory and regression-test chains do not start the inherited Tor bridge automatically.

The shutdown window is explicitly destroyed before event-tracking state. Its final Qt events previously accessed an already-freed string and caused a double free after the test suite finished. Clearing send recipients also releases the removed layout items.

The test application disables the GUI stall watchdog because synchronous test block generation under memory checking can exceed a minute. The normal wallet constructor keeps the watchdog enabled. All Qt checks still run. Transaction validation and proof rules remain unchanged.

The branding transform updates send/receive display labels while preserving the internal asset filter identifier. It is repeatable, so cached builds do not accumulate duplicate lab titles.

## Memory checks

```sh
docker build -f protocol/docker/WalletCheck.Dockerfile -t noet-wallet:check protocol
docker run --rm --network none noet-wallet:check
```

The diagnostic image runs the same full Qt test binary with a ten-minute limit. It disables Qt regex JIT only for instrumentation, following [Qt's debugging guidance](https://doc.qt.io/qt-6/qregularexpression.html#debugging-code-that-uses-qregularexpression). It does not suppress wallet memory errors. A passing functional summary is insufficient: inspect the process exit code and Valgrind summary as well.

## Release separation

The launcher deliberately pins the laboratory chain. Do not change one flag to call this mainnet. A production release requires a separately reviewed network identity, enforced real inference proofs, approved economics, pinned model weights, independent verification, signed platform distributions and a security review. Preserve upstream copyright and license notices. Branding edits are restricted to selected display strings and the application icon; they do not constitute a complete audit of upstream UI functionality or external integrations.

See [RELEASE-PLAN.md](RELEASE-PLAN.md) for the desktop, mobile and browser-extension delivery order and recovery/signing requirements.
