# NOET miner

Mine NOET with an NVIDIA GPU. Each block is produced by running a pinned
language model (Qwen3-0.6B, and Qwen3-14B from block 3,500) on a research prompt; the proof of that inference,
plus a time-lock (VDF), is checked by the network's verifier. Block rewards are
paid by consensus directly to your own address.

## What you need

- An NVIDIA GPU with 8 GB or more of memory, and from block 3,500 at least
  34 GiB (see below). Tested on RTX A5000 and A40
  (compute capability 8.6). Other architectures (8.0, 8.9, 9.0, 12.0) are
  built but not yet validated against the network verifier.
- NVIDIA driver 570 or newer.
- A NOET address starting with `noet1`. Create one in
  [Companion](https://www.noet.network/#wallet/app), the NOET wallet.

## Run with Docker (recommended)

```sh
python3 protocol/miner/prepare.py --out noet-miner
docker build -t noet-miner noet-miner
docker run --rm --gpus all noet-miner --address noet1... --check    # GPU, driver, model, gateway
docker run --gpus all --restart unless-stopped -v noet-mining:/work noet-miner --address noet1...
```

`--check` tests the GPU (driver 570 or newer, a supported architecture, 6 GiB
of memory), the engine, the model checksum, free disk and the gateway, and
says in plain words what to fix. It leases no work. While mining, the miner
waits out gateway outages with growing pauses (up to five minutes) and stops
cleanly on Ctrl-C or `docker stop`.

The build compiles the engine for current GPU architectures and rebuilds the
model file from the pinned Hugging Face revision. It fails if the result
differs by a single byte from the pinned SHA-256
`8fa11f4d815167c4f6e3c91b25aebde25d8346a8f11228e744016592532b0700`.

To build on GitHub instead (an x86 runner compiles CUDA without a GPU), copy
`protocol/ci/miner-image.yml` to `.github/workflows/` and run it from the
Actions tab; it pushes to `ghcr.io/<owner>/noet-miner` only when asked.

## Run without Docker

On Ubuntu 22.04 or 24.04 with the CUDA toolkit 12.4+:

```sh
cd noet-miner
sudo ./setup.sh          # build tools (once)
./setup.sh --build       # engine for your GPU, VDF binding, model
./mine.sh noet1... --check
./mine.sh noet1...
```

## Model switch to Qwen3-14B

NOET Mainnet moves from Qwen3-0.6B to Qwen3-14B at block 3,500
(`protocol/design/MODEL-SWITCH.md`). From that block, mining needs:

- a GPU with at least 34 GiB of memory: a 48 GB card (A40, RTX A6000, L40S) or
  an 80 GB card. 24 GB cards cannot run the 14B model.
- the Qwen3-14B model file (29.6 GB, SHA-256
  `b4ac198af720a0d4359a084f162f75553a1050e82869c96e583d3104f599dbe9`), built
  from the pinned Hugging Face revision like the 0.6B file. The conversion
  needs about 90 GB of free disk.

```sh
./convert.sh . models qwen3-14b                       # without Docker
docker build --build-arg WITH_14B=1 -t noet-miner .   # with Docker
```

With both model files the miner switches at block 3,500 by itself. With only
the 0.6B file it stops there and says what to do. Miners released before the
switch was set stop when the node upgrade lists Qwen3-14B; update them first.

## What happens

1. The miner asks the gateway for work, giving your address. The node builds a
   block template whose reward output pays that address.
2. Your GPU runs the model and the VDF and searches for a proof below the
   network target. A typical attempt takes a few minutes.
3. A winning proof is submitted. The node runs its quick checks, then the
   network verifier replays the inference before the block is final.
4. Rewards mature after 100 blocks and appear in your wallet.

Each attempt is recorded in the work directory (`attempt-*/result.json`).
The miner never retries a rejected proof with a different seed or target.

## Limits

The gateway allows 360 work leases (one per attempt) and 120 submissions per
hour per address range, and up to 8 open leases at a time. One GPU makes about
150 attempts an hour, so it never waits on these limits.

## Security

The miner holds no keys and needs no wallet password; it only knows your
public address. It connects only to the gateway you choose, over HTTPS, and
checks the network's genesis and model before working.
