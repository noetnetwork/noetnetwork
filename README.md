# NOET

NOET is a proof-of-work network where every block comes from running a pinned
language model and proving the inference. From block 3,500 that model is
Qwen3-14B; blocks before it use Qwen3-0.6B. Mainnet has been live since
15 October 2026.

## Mine

You need an NVIDIA GPU with at least 34 GiB of memory (a 48 GB card such as
the A40, RTX A6000 or L40S, or an 80 GB card), driver 570 or newer, Docker,
about 90 GB of free disk for the model build, and a `noet1...` address from the
wallet at https://www.noet.network.

```sh
python3 protocol/miner/prepare.py --out noet-miner
docker build --build-arg WITH_14B=1 -t noet-miner noet-miner
docker run --rm --gpus all noet-miner --address noet1... --check
docker run --gpus all --restart unless-stopped -v noet-mining:/work noet-miner --address noet1...
```

`--check` tests the GPU, driver, model and gateway and tells you what to fix.
The image carries both models and moves to Qwen3-14B at block 3,500 by
itself. Mining without Docker: [protocol/miner/README.md](protocol/miner/README.md).
The switch: [protocol/design/MODEL-SWITCH.md](protocol/design/MODEL-SWITCH.md).

## Build and test

```sh
python3 protocol/bootstrap.py
cd protocol && python3 -m unittest discover -s tests
```

`bootstrap.py` fetches the pinned upstream sources (`protocol/upstream.lock.json`)
and applies the patches in `protocol/patches/`.

## Layout

- `protocol/miner/`: the GPU miner
- `protocol/noet/`: mining gateway, explorer indexer, verifier transport, wallet tools
- `protocol/patches/`: changes to the pinned node and inference engine
- `protocol/mainnet/`: the `noet-mainnet-1` network profile
- `protocol/WHITEPAPER.md`: how it works

## Security

The code has not had an independent security audit. Report vulnerabilities
privately, as described in [SECURITY.md](SECURITY.md).

## Licence

Apache License 2.0 (`LICENSE`). The patches to bcore and llama.cpp
(`noet-lab.patch`, `noet-wallet.patch`, `noet-engine.patch`) are MIT licensed.
Third-party notices are in `NOTICE` and `LICENSES/`.
