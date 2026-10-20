// Development acceptance for the pinned native VDF, not AI proof validation.
#include "verifier.h"
#include "prover_slow.h"

static bool verify_proof(std::vector<uint8_t> challenge, const std::vector<uint8_t>& proof,
                         uint64_t iterations) {
    try {
        integer D = CreateDiscriminant(challenge, 1024);
        form x = form::from_abd(integer(2), integer(1), D);
        x.reduce();
        auto initial = SerializeForm(x, int(D.num_bits()));
        return CheckProofOfTimeNWesolowski(D, initial.data(), proof.data(),
                                         int32_t(proof.size()), iterations, 1024, 0);
    } catch (...) { return false; }
}

int main() {
    std::vector<uint8_t> challenge(32);
    for (size_t i = 0; i < challenge.size(); ++i) challenge[i] = uint8_t(i);
    const uint64_t iterations = 10000;
    integer D = CreateDiscriminant(challenge, 1024);
    form x = form::from_abd(integer(2), integer(1), D);
    x.reduce();
    auto proof = ProveSlow(D, x, iterations, "");
    if (proof.size() != 200 || !verify_proof(challenge, proof, iterations)) return 1;
    unsigned rejected = 0;
    auto wrong_challenge = challenge; wrong_challenge[0] ^= 1;
    if (verify_proof(wrong_challenge, proof, iterations)) return 2;
    ++rejected;
    if (verify_proof(challenge, proof, iterations + 1)) return 3;
    ++rejected;
    // Mutate every byte across both output and proof forms.
    for (size_t offset = 0; offset < proof.size(); ++offset) {
        auto changed = proof; changed[offset] ^= 1;
        if (verify_proof(challenge, changed, iterations)) return 4;
        ++rejected;
    }
    auto truncated = proof; truncated.pop_back();
    if (verify_proof(challenge, truncated, iterations)) return 5;
    ++rejected;
    auto extended = proof; extended.push_back(0);
    if (verify_proof(challenge, extended, iterations)) return 6;
    ++rejected;
    std::cout << "{\"iterations\":" << iterations << ",\"proofBytes\":" << proof.size()
              << ",\"validProofAccepted\":true,\"invalidCasesRejected\":" << rejected
              << ",\"proofHex\":\"";
    for (auto value : proof) std::cout << std::hex << std::setw(2) << std::setfill('0') << unsigned(value);
    std::cout << "\"}\n";
}
