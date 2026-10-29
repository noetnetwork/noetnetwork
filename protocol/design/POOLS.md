# Mining pools and miner names

Draft, 29 October 2026.

## Why pools are harder here

In Bitcoin a pool hands out work with an easier share target. A share is
checked with one hash, so the pool can count every miner's effort cheaply.
In NOET a proof that meets a share target proves almost nothing until it is
replayed, and replay costs a GPU for 20 s (0.6B) to 4 minutes (14B) per
proof. A pool that pays for unchecked shares pays for fake work.

## A pool protocol that holds up

1. **Template.** The pool runs its own gateway against its own node and
   leases work whose coinbase pays the pool's address (the gateway already
   does this for any address).
2. **Shares.** The pool sets a share target, for example 64 times easier than
   the network's. Miners submit shares to the pool. The pool runs the Quick
   checks on every share (VDF, target, context, model): cheap, CPU only.
3. **Sampling.** The pool replays a random sample of shares on its own GPU,
   for example 1 in 20 per miner. A RED share forfeits that miner's unpaid
   balance and bans the address for a day. Every winning share (one that
   meets the network target) goes to the network's verifier as usual.
4. **Payout.** Pay per last N shares (PPLNS) over Quick-checked shares, after
   the block matures (100 blocks), from the pool's address, with the pool
   fee stated up front.

Cost: at 1 in 20 sampling, a pool needs about one verifier GPU per 20 miner
GPUs of the same model size. That is the price of trusting shares, and the
reason a solo miner with one GPU earns less steadily but pays no one.

## What the network provides today

Solo mining only: each lease pays the address the miner gives, and each IPv4
address gets 360 leases an hour (`noet/gateway.py`). Nothing in consensus
stops a pool from forming as above; it needs a pool operator, not a protocol
change.

## Miner names and reliability

The explorer's producer chart counts blocks per reward address. To show a
name, a miner proves control of the address by signing a short message
(`NOET name: <name>, <date>`) with the address's key, in the wallet; the
explorer verifies the signature and shows the name next to the address.
Names are opt-in, can be removed by signing an empty name, and are checked
against a list of reserved words. Reliability per address (blocks, share of
the last 144, Full GREEN rate, and the answer delivery rate from
`USEFUL-WORK-NEXT.md`) can be shown without any name.

Needs: message signing in the wallet core (BIP322-style for segwit
addresses) and a small signed-names store in the indexer.
