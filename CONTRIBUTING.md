# Contributing

Thank you for helping. A few ground rules keep consensus code reviewable.

## Before you start

- Open an issue for anything that changes consensus (block validity, rewards,
  allocations, proof formats) before writing code. Such changes take effect
  only at an upgrade height announced in advance, which every node, verifier
  and miner must adopt, so they are batched.
- Economics live in one place: `protocol/economics/tokenomics.json`. Run
  `python3 protocol/economics/tokenomics.py --write` after changing it.

## Making a change

```sh
python3 protocol/bootstrap.py                 # pinned upstream sources and patches
cd protocol && python3 -m unittest discover -s tests
```

- Keep pull requests small and focused, with tests for new behaviour.
- Changes to upstream code go in `protocol/patches/` and must apply cleanly to
  the pinned commit in `protocol/upstream.lock.json`.
- Never commit keys, tokens, wallet files or hosted-service credentials.

## Licence

By contributing you agree that your contribution is licensed under the Apache
License 2.0 (patches to bcore and llama.cpp under the MIT License), as described
in `README.md`.
