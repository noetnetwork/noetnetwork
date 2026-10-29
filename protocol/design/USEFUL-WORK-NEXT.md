# Useful work: next steps

Draft, 29 October 2026. Builds on `USEFUL-WORK-JOBS.md` (how public requests
and open-dataset prompts become the inference behind proofs) and on what the
gateway does today: it queues public prompts from www.noet.network/#ask,
hands one to each lease, and stores the miner's answer.

## 1. Answer quality, per miner

Replay proves the model ran; it does not say the answer is good, because the
pinned model's answer is what it is. What can differ between miners is
truncation and whether an answer came back at all. Proposed measures, shown
per reward address in the explorer:

- **Delivery rate.** Leases with a task that returned an answer, over leases
  with a task (the gateway has both counts).
- **Truncation rate.** Answers marked `truncated`.
- **Spot checks.** For 1 in 50 answered tasks, the verifier (or any second
  GPU) reruns the same prompt and sampler seed and compares the text. A
  mismatch is a fault in that miner's engine, worth showing, not a
  consensus failure.

## 2. Sponsored prompts

Paying for priority in the queue needs a payment rail. The simplest that
needs no new code in consensus: a NOET payment to a per-request address that
the gateway watches, then the prompt jumps the queue once the payment has one
confirmation. Open questions before building it: refunds when no miner
answers, abuse of priority for spam, and whether paid prompts may stay
private (today all prompts are public). `economics/README.md` warns that
self-purchased jobs must never buy extra subsidy; priority changes the order
of work only, never the reward.

## 3. Richer task types

The proof covers a sequence of generated tokens. Task types that fit the
same proof without format changes:

- **Classification and extraction**: the prompt asks for a label or JSON;
  the answer is the generated text.
- **Longer contexts**: limited by the 516-token windows the producer runs and
  the verifier's memory; needs a measured replay cost per context length.

Embeddings do not fit: they are not a sampled sequence, so the current proof
cannot bind them. They would need a new proof version.

## 4. Replay cost

Full replay of a 14B proof took 224 to 275 s on an A100 (median 242 s),
against about 20 s for 0.6B (`validation/model-switch-rehearsal.json`).
Candidates to measure, each against the same recorded proofs:

- keeping the weights resident between requests (first measure how much of
  a replay is spent outside the forward pass);
- batching several pending proofs into one forward pass when blocks arrive
  together;
- caching the prompt's key-value state across proofs that share a prompt
  prefix.

None may change a verdict: the conformance suite (`GOVERNANCE.md`) must give
identical GREEN and RED results and peak sigma within 0.5.

## 5. Third-party verifiers

For an outsider to run an auditing verifier they need: the verifier image
(built by `prepare_verifier.py` from pinned sources), the approved model
snapshots and manifests, an 80 GB GPU from block 3,500, and a way to receive
proofs. The last part is new: an auditing verifier does not sit on the
node's relay. Proposed: the explorer publishes each block's proof bytes
(today `/proof/<powHash>` finds the block that carries a proof, not the
bytes; whether the node keeps them after connecting is to be checked), and
the auditor replays from those and publishes signed attestations
(`ATTESTATIONS.md`).

## 6. Verify a block yourself

The guide to write once item 5 exists, in this order:

1. Fetch the block and its proof from the public API.
2. Check the header chain and the Quick rules on a CPU (VDF, target, model
   for the height).
3. Replay on a GPU with the verifier image and compare the verdict with the
   published attestation.

Steps 1 and 2 can be a script that needs no GPU; step 3 needs the verifier
image and is the expensive part. Writing step 3 as one command waits on a
published proof download, because a guide that cannot be run would mislead.
