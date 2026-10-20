"""Independent Python implementation of the testnet emission (mirrors policy.h).

Kept separate from the C++ so a consensus check compares two implementations.
Only the constants are shared, from protocol/economics/tokenomics.json.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'economics'))
from tokenomics import COIN, EPOCH_BLOCKS, MINING, RELEASE_BASIS_POINTS, RELEASE_PERCENT  # noqa: E402,F401


def _epochs():
    remaining, before, rows = MINING, 0, []
    while remaining:
        budget = max(1, remaining * RELEASE_BASIS_POINTS // 10000)
        budget = min(budget, remaining)
        rows.append((before, budget))
        before += budget
        remaining -= budget
    return rows


EPOCHS = _epochs()


def mining_issued(height):
    epoch, offset = divmod(height, EPOCH_BLOCKS)
    if epoch >= len(EPOCHS):
        return MINING
    before, budget = EPOCHS[epoch]
    return before + budget * offset // EPOCH_BLOCKS


def subsidy(height):
    return 0 if height <= 0 else mining_issued(height) - mining_issued(height - 1)
