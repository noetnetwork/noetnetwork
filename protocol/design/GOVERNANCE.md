# Models, upgrades and versions

Draft, 29 October 2026. How NOET adds, pins, announces and retires a
consensus model, and how any consensus change is signalled and activated.
Nothing here is in force until the owner confirms it; until then the only
precedent is the Qwen3-14B switch (`MODEL-SWITCH.md`).

## 1. Model lifecycle

| Stage | What must exist | Who decides |
|---|---|---|
| Candidate | A profile in `models/profiles.json`: name, immutable revision, manifest digest, reproducible GGUF conversion with its digest | anyone may propose |
| Measured | Validation records on the verifier's hardware: honest proofs' peak sigma against the 12 sigma cap, Full replay time and peak GPU memory, and RED for proofs made with other weights (as in `validation/model-qwen3-14b.json` and `model-switch-rehearsal.json`) | evidence |
| Scheduled | `modelSwitch` height and `targetScale` in `mainnet/network.json`, a node build with the height, a verifier with both snapshots, and a miner release that pins the switch | owner, explicitly |
| Announced | A notice on the site, in the miner (`switch-ahead`, 288 blocks before) and to miners' alert webhooks | operator |
| Active | Blocks from the height carry the new model | consensus |
| Retired | No new blocks use it. Verifiers keep its snapshot so the chain can be replayed from genesis | never deleted |

Notice: at least one week between announcing a height and reaching it, and
at least two weeks when miners need new hardware (the 14B switch drops 24 GB
cards). The height is chosen so that the week holds at the current block
rate, with margin for a fast week.

Next candidate after 14B: Qwen3-32B, only once a verifier with enough GPU
memory for an fp32 replay is measured and funded.

## 2. Signalling and activation

Today every consensus change is a hard fork at a height written into the
node build: operators upgrade before the height or fall off the chain. That
is workable with one operator and wrong with many. Proposed for the first
change that happens after other operators run nodes:

1. **Version bits.** Each proposal gets a bit in the block version and a
   start height. Miners signal readiness by setting the bit; the gateway sets
   it in templates once the node it runs has the change.
2. **Lock-in.** When 90% of blocks in one 2,016-block window signal, the
   change locks in, and activates one window later.
3. **Timeout.** A proposal that does not lock in by its timeout height
   expires and needs a new bit.

This is the BIP9 mechanism the node already inherits; NOET would use it, not
invent one. Model switches can keep a fixed height, because miners must
prepare files and hardware either way, but should also carry a signalling bit
so operators can see readiness on chain before the height.

## 3. Versions

- **Protocol**: the specification version in `SPEC.md` (major for any change
  to validity).
- **Node, verifier, miner**: each release names the protocol version it
  implements; the miner reports its own version to the gateway, which
  publishes counts per version for the last day (`minerVersions` in
  `/mining/info`).
- **Ruleset**: the verifier ruleset the node names per height, as today.

## 4. Engine builds

Every engine and verifier build that can produce or judge proofs should pass
one conformance suite before release:

- a fixed set of honest proofs per model, which must stay GREEN with their
  recorded peak sigma within 0.5 of the previous build;
- proofs made with other weights or altered logits, which must be RED;
- proofs with altered context, VDF or model identifier, which must fail the
  quick checks;
- the detector's refusal cases (inputs wider than its 128 slots), which must
  abstain.

The inputs exist in the validation records and rehearsal scripts
(`gpu/stage.py`, `native.py`); the suite needs a GPU and so runs on demand,
or nightly once a GPU runner is funded.
