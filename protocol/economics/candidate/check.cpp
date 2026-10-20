#include "policy.h"
#include <iostream>
#include <limits>

int main() {
    using namespace noet::candidate;
    Amount total = 0;
    for (std::uint64_t height = 1; height <= EPOCHS.size() * EPOCH_BLOCKS; ++height) {
        const auto reward = subsidy(height);
        total += reward;
        const auto state = supply(height);
        if (total != state.mining || state.minted > CAP || state.unlocked_upper_bound > state.minted) return 1;
        const auto epoch = (height - 1) / EPOCH_BLOCKS;
        const auto quotient = EPOCHS[epoch].budget / EPOCH_BLOCKS;
        if (reward < quotient || reward > quotient + 1) return 2;
    }
    if (total != MINING || subsidy(EPOCHS.size() * EPOCH_BLOCKS + 1) != 0) return 3;
    std::uint64_t height;
    while (std::cin >> height) {
        const auto state = supply(height);
        std::cout << height << ',' << subsidy(height) << ',' << state.mining << ',' << state.minted;
        for (const auto amount : state.unlocked) std::cout << ',' << amount;
        std::cout << ',' << state.unlocked_upper_bound << '\n';
    }
}
