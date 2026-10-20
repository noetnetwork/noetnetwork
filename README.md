# NOET

NOET is a proof-of-work network where every block comes from running a pinned
language model (Qwen3-0.6B) and proving the inference. Mainnet has been live
since 15 October 2026.

## Mine

You need an NVIDIA GPU with 8 GB or more, driver 570 or newer, Docker, and a
`noet1...` address from the wallet at https://www.noet.network.

```sh
python3 protocol/miner/prepare.py --out noet-miner
docker build -t noet-miner noet-miner
docker run --rm --gpus all noet-miner --address noet1... --check
docker run --gpus all --restart unless-stopped -v noet-mining:/work noet-miner --address noet1...
```

`--check` tests the GPU, driver, model and gateway and tells you what to fix.
Mining without Docker: [protocol/miner/README.md](protocol/miner/README.md).

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
