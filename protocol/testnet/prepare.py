"""Source overlay for a public NOET network: the testnet (noet-testnet-1) or,
once its genesis time and real allocation signers are set, mainnet
(protocol/mainnet/network.json).

Starts from the reviewed laboratory sources and changes only:
  * network identity: genesis time, magic, port, tnoet addresses;
  * consensus economics: 100M money range, the proposal's emission curve
    (15% of the remaining 80M mining reserve per 52,560-block epoch) and the
    exact 20M block-1 allocation outputs (169 time-locked 2-of-3 installments);
  * the coinbase template, so block 1 pays those allocations;
  * difficulty: per-block ASERT toward 600 s blocks (12-hour half-life) from
    the network's `asertHeight`; upstream's regtest family never retargets;
  * startup, which requires real external verification (release variant).
The `test` variant keeps mock validation and the laboratory proof activation
heights for local consensus tests only; economics and identity are identical.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'testnet'))
from allocations import OUTPUTS, header as allocation_header, outputs  # noqa: E402
from tokenomics import ALLOCATION_ATOMS, CAP, COIN, EPOCH_BLOCKS, MINING, RELEASE_PERCENT, policy_text  # noqa: E402

def emission(percent=RELEASE_PERCENT):
    return (f'{percent:g}% of the remaining {MINING // COIN // 1_000_000}M mining reserve per\n'
            f'        // {EPOCH_BLOCKS:,}-block epoch')


EMISSION = emission()

PROFILE = 'noet-testnet-1'
MODEL = 'Qwen/Qwen3-0.6B'
REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
GENESIS_TIME = 1791763200  # 12 October 2026 00:00 UTC
GENESIS_NONCE = 4  # first nonce whose short header meets 0x207fffff
# Difficulty: upstream's staged ASERT (pow.cpp, DAA v2), pinned for NOET. It
# starts at the minimum difficulty (no reset) and halves or doubles the target
# for every 12 hours the chain runs behind or ahead of 600-second blocks.
# The testnet ran without retargeting until this height (a scheduled fork).
TESTNET_ASERT_HEIGHT = 1500
ASERT_HALF_LIFE = 12 * 60 * 60
ASERT_ANCHOR_BITS = 0x207fffff  # powLimit, the difficulty every block had before
ASERT_PHASE_B_OFFSET = 144  # upstream requires a second phase; NOET uses the same half-life

# Network identity. The laboratory chain uses magic 4e 4f 45 54, port 29441,
# nlab addresses and base58 prefixes 0x35/0x75/0xB5; every public network differs.
TESTNET = {'name': 'testnet', 'profile': PROFILE, 'magic': (0x54, 0x53, 0x31), 'port': 33441,
           'genesisTime': GENESIS_TIME, 'genesisNonce': GENESIS_NONCE, 'hrp': 'tnoet',
           'base58': (0x41, 0x81, 0xC1), 'bip32': 0x54, 'releasePercent': RELEASE_PERCENT,
           'asertHeight': TESTNET_ASERT_HEIGHT, 'keys': ROOT / 'testnet/allocation-keys.json'}


def mainnet(path=ROOT / 'mainnet/network.json'):
    """The mainnet definition; refuses until its genesis and real signers are set."""
    spec = json.loads(Path(path).read_text())
    if not isinstance(spec.get('genesisTime'), int) or not isinstance(spec.get('genesisNonce'), int):
        raise ValueError('Mainnet genesis is not set yet: run protocol/mainnet/genesis.py --time UNIX_TIME')
    seeds = spec.get('seeds', [])
    if not all(isinstance(s, str) and re.fullmatch(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+', s)
               for s in seeds):
        raise ValueError('Mainnet seeds must be DNS host names')
    asert = spec.get('asertHeight')
    if isinstance(asert, bool) or not isinstance(asert, int) or asert < 2:
        # Height 1 would anchor the schedule at the genesis time, long before launch.
        raise ValueError('Mainnet asertHeight must be an integer of at least 2')
    keys = ROOT / spec['allocationKeys']
    if not keys.exists():
        raise ValueError('Mainnet allocation signers are not set yet: run '
                         'protocol/testnet/set_allocation_keys.py --network mainnet ...')
    return {'name': 'mainnet', 'profile': spec['profile'], 'magic': tuple(int(b, 16) for b in spec['magic']),
            'port': spec['p2pPort'], 'genesisTime': spec['genesisTime'], 'genesisNonce': spec['genesisNonce'],
            'hrp': spec['hrp'], 'base58': tuple(int(b, 16) for b in spec['base58']),
            'bip32': int(spec['bip32'], 16), 'releasePercent': spec.get('releasePercent', RELEASE_PERCENT),
            'seeds': seeds, 'asertHeight': asert, 'keys': keys}
FILES = ('src/kernel/chainparams.cpp', 'src/init.cpp', 'src/consensus/amount.h', 'src/consensus/params.h',
         'src/validation.cpp', 'src/node/miner.cpp')


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Expected exactly one source anchor: ' + old[:100])
    return text.replace(old, new, 1)


def chainparams(text, test, net=TESTNET):
    start = text.index('class CTensorRegParams')
    end = text.index('\n};', start) + 3
    block = text[start:end]
    for old, new in (
        ('pchMessageStart[1] = 0x4f;', f'pchMessageStart[1] = 0x{net["magic"][0]:02x};'),
        ('pchMessageStart[2] = 0x45;', f'pchMessageStart[2] = 0x{net["magic"][1]:02x};'),
        ('pchMessageStart[3] = 0x54;', f'pchMessageStart[3] = 0x{net["magic"][2]:02x};'),
        ('nDefaultPort = 29441;', f'nDefaultPort = {net["port"]};'),
        ('CreateGenesisBlock(1791676800, 2,', f'CreateGenesisBlock({net["genesisTime"]}, {net["genesisNonce"]},'),
        ('consensus.DefaultModelName = "testModel";', f'consensus.DefaultModelName = "{MODEL}";'),
        ('consensus.DefaultModelCommit = "testModelCommit";', f'consensus.DefaultModelCommit = "{REVISION}";'),
        ('genesis.pow.model_identifier = "testModel@testModelCommit";',
         'genesis.pow.model_identifier = consensus.DefaultModelName + "@" + consensus.DefaultModelCommit;'),
        ('bech32_hrp = "nlab";', f'bech32_hrp = "{net["hrp"]}";'),
        ('std::vector<unsigned char>(1,0x35)', f'std::vector<unsigned char>(1,0x{net["base58"][0]:02X})'),
        ('std::vector<unsigned char>(1,0x75)', f'std::vector<unsigned char>(1,0x{net["base58"][1]:02X})'),
        ('std::vector<unsigned char>(1,0xB5)', f'std::vector<unsigned char>(1,0x{net["base58"][2]:02X})'),
        ('{0x04, 0x72, 0x4e, 0x50}', f'{{0x04, 0x72, 0x{net["bip32"]:02x}, 0x50}}'),
        ('{0x04, 0x72, 0x4e, 0x58}', f'{{0x04, 0x72, 0x{net["bip32"]:02x}, 0x58}}'),
    ):
        block = replace_once(block, old, new)
    if net.get('seeds'):
        # DNS seeds: each name resolves to nodes listening on the default port.
        block = replace_once(block, '        vSeeds.clear();\n', '        vSeeds.clear();\n' + ''.join(
            f'        vSeeds.emplace_back("{seed}");\n' for seed in net['seeds']))
    daa = ('        consensus.DaaV2Height = ReadDaaV2HeightArg("-daav2height");\n'
           '        consensus.DaaV2PhaseBHeight = ReadDaaV2HeightArg("-daav2phasebheight");\n'
           '        consensus.DaaV2ReviewHeight = ReadDaaV2HeightArg("-daav2reviewheight");\n'
           '        consensus.DaaV2AnchorBitsPhaseA = ReadDaaV2AnchorBitsArg(consensus.DaaV2AnchorBitsPhaseA);\n')
    pinned = (f'        consensus.DaaV2HalfLifePhaseA = {ASERT_HALF_LIFE};\n'
              f'        consensus.DaaV2HalfLifePhaseB = {ASERT_HALF_LIFE};\n')
    if test:
        # Mock chains mine instantly, so ASERT stays off unless a test sets -daav2height.
        block = replace_once(block, daa, daa.replace('ReadDaaV2AnchorBitsArg(consensus.DaaV2AnchorBitsPhaseA)',
                                                     f'ReadDaaV2AnchorBitsArg(0x{ASERT_ANCHOR_BITS:08x})') + pinned)
    else:
        start_height = net['asertHeight']
        block = replace_once(block, daa, (
            f'        // NOET difficulty: ASERT from height {start_height}, 600 s blocks, 12-hour half-life.\n'
            f'        consensus.DaaV2Height = {start_height};\n'
            f'        consensus.DaaV2PhaseBHeight = {start_height + ASERT_PHASE_B_OFFSET};\n'
            '        consensus.DaaV2ReviewHeight = std::numeric_limits<int>::max();\n'
            f'        consensus.DaaV2AnchorBitsPhaseA = 0x{ASERT_ANCHOR_BITS:08x};\n') + pinned)
    if test:
        # Mock blocks cannot carry inference proofs, so the test variant keeps the
        # laboratory proof activation heights. Economics and identity are identical.
        return text[:start] + block + text[end:]
    block = replace_once(block, 'm_is_mockable_chain = true;',
                         'm_is_mockable_chain = false;\n        consensus.external_api = true;')
    for field, value in {'V3ActivationHeight': 1, 'AntiParrotHeight': 1, 'PadSlotZeroHeight': 1,
                         'StepBindHeight': 1, 'StepBindGraceEndTime': 0, 'ProofV4Height': 1,
                         'V3PenaltyHeight': 2, 'V3SunsetHeight': 3, 'ProofHistoryCollectHeight': 1,
                         'ProofHistoryEnforceHeight': 2, 'nEnforcedProofWindowSize': 256}.items():
        block, count = re.subn(r'consensus\.' + field + r' = [^;]+;', f'consensus.{field} = {value};', block)
        if count != 1:
            raise ValueError('Unexpected activation field: ' + field)
    return text[:start] + block + text[end:]


def params(text):
    # Upstream couples a one-off difficulty reset to V3PenaltyHeight, which NOET
    # chains pass at height 2, before ASERT starts. NOET has no such reset.
    return replace_once(text, 'int DaaV2PhaseCHeight() const { return V3PenaltyHeight; }',
                        'int DaaV2PhaseCHeight() const { return std::numeric_limits<int>::max(); } // NOET: no phase-C reset')


def init(text, test, net=TESTNET):
    if test:
        return text
    anchor = '    const std::string api_mode = to_lower(args.GetArg("-validationapi", "real"));'
    return replace_once(text, anchor, anchor + f'''
    // The public {net["name"]} only runs with its own chain and real external verification.
    if (chainparams.GetChainType() != ChainType::TENSOR_REG || api_mode != "real" ||
        !args.GetBoolArg("-useextapi", true)) {{
        return ChainstateLoadResult{{ChainstateLoadStatus::FAILURE_FATAL,
            Untranslated("NOET {net["name"]} requires its own chain, real validation and external verification")}};
    }}
''')


def amount(text, net=TESTNET):
    return replace_once(text, 'static constexpr CAmount MAX_MONEY = 21000000 * COIN;',
                        f'static constexpr CAmount MAX_MONEY = {CAP // COIN} * COIN; // NOET {net["name"]} cap')


def validation(text, net=TESTNET):
    n = net['name']
    text = replace_once(text, '#include <validation.h>\n', f'#include <validation.h>\n#include <noet_{n}.h>\n')
    start = text.index('    if (consensusParams.tensor_subsidy) {')
    end = text.index('        return reward;\n    }\n', start) + len('        return reward;\n    }\n')
    text = text[:start] + f'''    if (consensusParams.tensor_subsidy) {{
        // NOET {n} emission: {emission(net['releasePercent'])}, distributed per block with exact integer arithmetic.
        return noet::{n}::Subsidy(nHeight);
    }}
''' + text[end:]
    return replace_once(
        text,
        '    CAmount blockReward = nFees + GetBlockSubsidy(pindex->nHeight, params.GetConsensus());\n',
        '''    CAmount blockReward = nFees + GetBlockSubsidy(pindex->nHeight, params.GetConsensus());
    // NOET NET: block 1 issues the ALLOCATION_MILLIONM allocation outputs, exactly and in order.
    if (pindex->nHeight == 1) {
        blockReward += noet::NET::ALLOCATION_TOTAL;
        if (!noet::NET::CarriesAllocations(*block.vtx[0]) && state.IsValid()) {
            state.Invalid(BlockValidationResult::BLOCK_CONSENSUS, "bad-cb-allocation",
                          "block 1 must pay the NET allocation outputs exactly");
        }
    }
'''.replace('ALLOCATION_MILLION', str(ALLOCATION_ATOMS // COIN // 1_000_000)).replace('NET', n))


def miner(text, net=TESTNET):
    n = net['name']
    text = replace_once(text, '#include <node/miner.h>\n', f'#include <node/miner.h>\n#include <noet_{n}.h>\n')
    return replace_once(
        text,
        '        coinbaseTx.vout[0].nValue = nFees + GetBlockSubsidy(nHeight, chainparams.GetConsensus());\n',
        f'''        coinbaseTx.vout[0].nValue = nFees + GetBlockSubsidy(nHeight, chainparams.GetConsensus());
        if (nHeight == 1) noet::{n}::AppendAllocations(coinbaseTx);
''')


SUPPORT = '''// NOET public testnet economics. Shared by validation and block assembly.
#pragma once
#include <consensus/amount.h>
#include <primitives/transaction.h>
#include <script/script.h>
#include <util/strencodings.h>
#include <noet_testnet_allocations.h>
#include <noet_testnet_policy.h>

namespace noet::testnet {
inline CAmount Subsidy(int height)
{
    return height <= 0 ? 0 : static_cast<CAmount>(noet::candidate::subsidy(static_cast<std::uint64_t>(height)));
}
inline CScript AllocationScript(const Allocation& allocation)
{
    const auto bytes = ParseHex(allocation.script_pub_key);
    return CScript(bytes.begin(), bytes.end());
}
inline void AppendAllocations(CMutableTransaction& coinbase)
{
    for (const auto& allocation : ALLOCATIONS) coinbase.vout.emplace_back(allocation.atoms, AllocationScript(allocation));
}
// Outputs 1..169 follow the miner's reward output, before any witness commitment.
inline bool CarriesAllocations(const CTransaction& coinbase)
{
    if (coinbase.vout.size() < ALLOCATIONS.size() + 1) return false;
    for (size_t i = 0; i < ALLOCATIONS.size(); ++i) {
        const CTxOut& out = coinbase.vout[i + 1];
        if (out.nValue != ALLOCATIONS[i].atoms || out.scriptPubKey != AllocationScript(ALLOCATIONS[i])) return false;
    }
    return true;
}
}  // namespace noet::testnet
'''


def support(net=TESTNET):
    n = net['name']
    text = SUPPORT.replace('NOET public testnet economics', f'NOET public {n} economics')
    for old in ('<noet_testnet_allocations.h>', '<noet_testnet_policy.h>', 'namespace noet::testnet'):
        text = text.replace(old, old.replace('testnet', n))
    return text.replace('}  // namespace noet::testnet', f'}}  // namespace noet::{n}')


def overlay(sources, test=False, net=TESTNET):
    """sources: {relative path: original text}. Returns {relative path: new text} including new headers."""
    n = net['name']
    result = {
        'src/kernel/chainparams.cpp': chainparams(sources['src/kernel/chainparams.cpp'], test, net),
        'src/init.cpp': init(sources['src/init.cpp'], test, net),
        'src/consensus/amount.h': amount(sources['src/consensus/amount.h'], net),
        'src/consensus/params.h': params(sources['src/consensus/params.h']),
        'src/validation.cpp': validation(sources['src/validation.cpp'], net),
        'src/node/miner.cpp': miner(sources['src/node/miner.cpp'], net),
        f'src/noet_{n}.h': support(net),
        f'src/noet_{n}_allocations.h': allocation_header(outputs(net['keys']), n),
        # A network may set its own release rate; the shared tokenomics otherwise.
        f'src/noet_{n}_policy.h': (ROOT / 'economics/candidate/policy.h').read_text()
        if net['releasePercent'] == RELEASE_PERCENT else policy_text(net['releasePercent']),
    }
    return result


def profile(sources, result, test=False, net=TESTNET):
    files = {}
    for path, text in result.items():
        before = sources.get(path)
        files[path] = {'beforeSha256': hashlib.sha256(before.encode()).hexdigest() if before is not None else None,
                       'afterSha256': hashlib.sha256(text.encode()).hexdigest()}
    return {'format': net['profile'], 'variant': 'test' if test else 'release', 'model': MODEL + '@' + REVISION,
            'genesisTime': net['genesisTime'], 'addressPrefix': net['hrp'], 'p2pPort': net['port'],
            'economics': {'capAtoms': str(CAP), 'miningAtoms': str(MINING),
                          'allocationAtoms': str(ALLOCATION_ATOMS), 'allocationOutputs': OUTPUTS,
                          'epochBlocks': EPOCH_BLOCKS,
                          'epochRelease': f'{net["releasePercent"]}% of remaining mining reserve'},
            'files': files, 'productionReady': False,
            'purpose': ('Public testnet with test coins; allocation signers are testnet-only keys'
                        if net['name'] == 'testnet' else
                        'Mainnet candidate; not production-ready until reviewed and audited')}


if __name__ == '__main__':
    core = ROOT / '.upstream/services/core-node/bcore'
    sources = {p: (core / p).read_text() for p in FILES}
    for variant in (False, True):
        result = overlay(sources, test=variant)
        print(json.dumps({k: v['afterSha256'][:16] for k, v in profile(sources, result, variant)['files'].items()}))
