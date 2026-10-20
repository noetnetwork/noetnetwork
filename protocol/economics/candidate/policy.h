// Unactivated monetary-policy candidate. Does not change any running chain.
#pragma once
#include <array>
#include <cstdint>

namespace noet::candidate {
using Amount = std::uint64_t;
constexpr Amount COIN = 100000000;
// One allocation class: total, amount eligible at issuance, cliff and linear duration in blocks.
struct Allocation { Amount total; Amount initial; std::uint64_t cliff; std::uint64_t duration; };
// BEGIN GENERATED from protocol/economics/tokenomics.json by tokenomics.py --write
constexpr Amount CAP = 100000000 * COIN;
constexpr Amount MINING = 80000000 * COIN;
constexpr std::uint64_t EPOCH_BLOCKS = 52560;
constexpr Amount RELEASE_BASIS_POINTS = 1500;  // 15%
constexpr std::uint64_t MONTH_BLOCKS = 4380;
constexpr std::array<Allocation, 3> ALLOCATIONS{{
    {8000000 * COIN, 2000000 * COIN, 0 * MONTH_BLOCKS, 150 * MONTH_BLOCKS},  // team
    {8000000 * COIN, 1000000 * COIN, 0 * MONTH_BLOCKS, 150 * MONTH_BLOCKS},  // treasury
    {4000000 * COIN, 2000000 * COIN, 0 * MONTH_BLOCKS, 150 * MONTH_BLOCKS},  // ecosystem
}};
constexpr std::size_t EXPECTED_EPOCHS = 224;
// END GENERATED

constexpr Amount allocation_total() {
    Amount total = 0;
    for (const auto& a : ALLOCATIONS) total += a.total;
    return total;
}
constexpr Amount annual_budget(Amount remaining) {
    if (!remaining) return 0;
    // floor(remaining * bp / 10000), split so the product cannot overflow.
    const Amount share = remaining / 10000 * RELEASE_BASIS_POINTS + remaining % 10000 * RELEASE_BASIS_POINTS / 10000;
    return share ? share : 1;
}
constexpr std::size_t epoch_count() {
    Amount remaining = MINING;
    std::size_t count = 0;
    while (remaining) { remaining -= annual_budget(remaining); ++count; }
    return count;
}
struct Epoch { Amount before; Amount budget; };
constexpr auto make_epochs() {
    std::array<Epoch, epoch_count()> result{};
    Amount remaining = MINING;
    for (auto& epoch : result) {
        epoch = {MINING - remaining, annual_budget(remaining)};
        remaining -= epoch.budget;
    }
    return result;
}
inline constexpr auto EPOCHS = make_epochs();

// floor(total * elapsed / duration), without an overflowing intermediate product.
// All internal totals and durations are fixed policy constants; elapsed is clamped.
constexpr Amount vested(Amount total, std::uint64_t elapsed, std::uint64_t duration) {
    if (elapsed >= duration) return total;
    return elapsed * (total / duration) + elapsed * (total % duration) / duration;
}
constexpr Amount mining_issued(std::uint64_t height) {
    const auto epoch = height / EPOCH_BLOCKS;
    if (epoch >= EPOCHS.size()) return MINING;
    return EPOCHS[epoch].before + vested(EPOCHS[epoch].budget, height % EPOCH_BLOCKS, EPOCH_BLOCKS);
}
constexpr Amount subsidy(std::uint64_t height) {
    if (!height) return 0; // Non-mining allocations are separate genesis outputs.
    return mining_issued(height) - mining_issued(height - 1);
}
constexpr Amount unlocked(const Allocation& a, std::uint64_t height) {
    return a.initial + (height <= a.cliff ? 0 : vested(a.total - a.initial, height - a.cliff, a.duration));
}
struct Supply {
    Amount mining;
    Amount minted;
    std::array<Amount, ALLOCATIONS.size()> unlocked;
    Amount unlocked_upper_bound;
};
constexpr Supply supply(std::uint64_t height) {
    Supply result{mining_issued(height), mining_issued(height) + allocation_total(), {}, mining_issued(height)};
    for (std::size_t i = 0; i < ALLOCATIONS.size(); ++i) {
        result.unlocked[i] = unlocked(ALLOCATIONS[i], height);
        result.unlocked_upper_bound += result.unlocked[i];
    }
    return result;
}
constexpr bool allocations_vest_exactly() {
    Amount initial = 0;
    for (std::size_t i = 0; i < ALLOCATIONS.size(); ++i) {
        const auto& a = ALLOCATIONS[i];
        initial += a.initial;
        if (supply(a.cliff).unlocked[i] != a.initial) return false;
        if (supply(a.cliff + a.duration).unlocked[i] != a.total) return false;
        if (a.initial < a.total && supply(a.cliff + a.duration - 1).unlocked[i] == a.total) return false;
    }
    return supply(0).unlocked_upper_bound == initial;
}
static_assert(allocation_total() + MINING == CAP);
static_assert(epoch_count() == EXPECTED_EPOCHS);
static_assert(supply(0).minted == CAP - MINING);
static_assert(allocations_vest_exactly());
static_assert(supply(EXPECTED_EPOCHS * EPOCH_BLOCKS).minted == CAP);
}
