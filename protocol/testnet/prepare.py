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
  * a scheduled model switch (`modelSwitch`, design/MODEL-SWITCH.md): from its
    height every block carries the switch model, earlier blocks the genesis
    model, and the difficulty target is eased once at that height;
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
# Model switch (design/MODEL-SWITCH.md). The test variant reads the height from
# -modelswitchheight and uses this target scale when network.json sets none.
SWITCH_TEST_SCALE = (5, 2)

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
    switch = model_switch(spec.get('modelSwitch'), asert)
    keys = ROOT / spec['allocationKeys']
    if not keys.exists():
        raise ValueError('Mainnet allocation signers are not set yet: run '
                         'protocol/testnet/set_allocation_keys.py --network mainnet ...')
    return {'name': 'mainnet', 'profile': spec['profile'], 'magic': tuple(int(b, 16) for b in spec['magic']),
            'port': spec['p2pPort'], 'genesisTime': spec['genesisTime'], 'genesisNonce': spec['genesisNonce'],
            'hrp': spec['hrp'], 'base58': tuple(int(b, 16) for b in spec['base58']),
            'bip32': int(spec['bip32'], 16), 'releasePercent': spec.get('releasePercent', RELEASE_PERCENT),
            'seeds': seeds, 'asertHeight': asert, 'keys': keys, 'modelSwitch': switch}


def model_switch(switch, asert):
    """network.json `modelSwitch`, checked: the model and revision always; the
    height (null until announced) and the one-off target scale [num, den]."""
    if switch is None:
        return None
    model, revision, height, scale = (switch.get(k) for k in ('model', 'revision', 'height', 'targetScale'))
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model) or \
            not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('modelSwitch needs an exact model name and a 40-character revision')
    if model == MODEL:
        raise ValueError('modelSwitch must name a different model')
    if scale is not None and not (isinstance(scale, list) and len(scale) == 2 and all(type(x) is int for x in scale)
                                  and 1 <= scale[1] <= scale[0] <= 4 * scale[1]):
        # Easing only, at most 4x: pow.cpp RetargetSafe assumes NUM <= 4 * DEN.
        raise ValueError('modelSwitch targetScale must be [num, den] with 1 <= num/den <= 4')
    if height is not None:
        if isinstance(height, bool) or not isinstance(height, int) or \
                not asert + ASERT_PHASE_B_OFFSET < height < 2 ** 31 - 1:
            raise ValueError('modelSwitch height must be an integer after the ASERT phase-B height')
        if scale is None:
            raise ValueError('Set modelSwitch targetScale together with its height')
    return {'model': model, 'revision': revision, 'height': height,
            'targetScale': tuple(scale) if scale is not None else None}


def applied_switch(net, test):
    """The model switch compiled into this build: in the test variant whenever the
    network defines one (height from -modelswitchheight); in a release only once
    network.json announces its height. None leaves the sources as before."""
    switch = net.get('modelSwitch')
    if switch is None or (not test and switch['height'] is None):
        return None
    return {**switch, 'targetScale': switch['targetScale'] or SWITCH_TEST_SCALE}


FILES = ('src/kernel/chainparams.cpp', 'src/init.cpp', 'src/consensus/amount.h', 'src/consensus/params.h',
         'src/validation.cpp', 'src/node/miner.cpp', 'src/modeldb.cpp', 'src/net_processing.cpp')


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
    switch = applied_switch(net, test)
    if switch:
        height = 'ReadDaaV2HeightArg("-modelswitchheight")' if test else str(switch['height'])
        block = switch_chainparams(block, REVISION, switch, height)
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


def switch_chainparams(block, revision, switch, height):
    """The switch model and its height, next to the genesis model's revision."""
    return replace_once(block, f'consensus.DefaultModelCommit = "{revision}";', f'''consensus.DefaultModelCommit = "{revision}";
        // NOET model switch (consensus/params.h ModelForHeight, design/MODEL-SWITCH.md).
        consensus.SwitchModelName = "{switch['model']}";
        consensus.SwitchModelCommit = "{switch['revision']}";
        consensus.ModelSwitchHeight = {height};''')


SWITCH_FILES = ('src/consensus/params.h', 'src/validation.cpp', 'src/node/miner.cpp', 'src/modeldb.cpp',
                'src/net_processing.cpp')


def switch_sources(sources, switch):
    """The switch's edits outside chainparams, on unmodified upstream sources (for
    an overlay that changes none of these files otherwise, like realwork/prepare.py)."""
    return {'src/consensus/params.h': params(sources['src/consensus/params.h'], switch),
            'src/validation.cpp': switch_validation(sources['src/validation.cpp']),
            'src/node/miner.cpp': switch_miner(sources['src/node/miner.cpp']),
            'src/modeldb.cpp': switch_modeldb(sources['src/modeldb.cpp']),
            'src/net_processing.cpp': switch_relay(sources['src/net_processing.cpp'])}


PHASE_C = 'int DaaV2PhaseCHeight() const { return V3PenaltyHeight; }'


def params(text, switch=None):
    # Upstream couples a one-off difficulty reset to V3PenaltyHeight, which NOET
    # chains pass at height 2, before ASERT starts. NOET has no such reset, except
    # at a scheduled model switch, where the new model makes every attempt slower.
    if switch is None:
        return replace_once(text, PHASE_C,
                            'int DaaV2PhaseCHeight() const { return std::numeric_limits<int>::max(); } // NOET: no phase-C reset')
    num, den = switch['targetScale']
    for name, value in (('NUM', num), ('DEN', den)):
        old = f'static constexpr uint64_t DAAV2_PHASE_C_TARGET_{name}{{{10 if name == "NUM" else 7}}};'
        text = replace_once(text, old, f'static constexpr uint64_t DAAV2_PHASE_C_TARGET_{name}{{{value}}}; // NOET model switch')
    return replace_once(text, PHASE_C, '''// NOET: the one-off reset (target x NUM/DEN, fresh ASERT anchor) is the model switch.
    int DaaV2PhaseCHeight() const { return ModelSwitchHeight; }
    // NOET model switch (design/MODEL-SWITCH.md): blocks below ModelSwitchHeight
    // carry the genesis model, blocks from it SwitchModelName@SwitchModelCommit.
    std::string SwitchModelName;
    std::string SwitchModelCommit;
    int ModelSwitchHeight{std::numeric_limits<int>::max()};
    bool ModelSwitchScheduled() const
    {
        return ModelSwitchHeight != std::numeric_limits<int>::max() && !SwitchModelName.empty() && !SwitchModelCommit.empty();
    }
    //! The model a block at `height` must carry; empty when no switch is scheduled.
    std::string ModelForHeight(int height) const
    {
        if (!ModelSwitchScheduled()) return "";
        return height >= ModelSwitchHeight ? SwitchModelName + "@" + SwitchModelCommit
                                           : DefaultModelName + "@" + DefaultModelCommit;
    }''')


def init(text, test, net=TESTNET):
    if test:
        if applied_switch(net, test) is None:
            return text
        anchor = '        argsman.AddArg("-daav2height=<n>",'
        return replace_once(text, anchor, '''        argsman.AddArg("-modelswitchheight=<n>", "NOET test chains only: height from which every block carries the switch model and the difficulty target is eased once (default: no switch).", ArgsManager::ALLOW_ANY, OptionsCategory::CHAINPARAMS);
''' + anchor)
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


def switch_validation(text):
    """One consensus model per height, and neither scheduled model can be banned."""
    text = replace_once(text, '''    // INERT LEVER (ASERT_CHANGE.md §5.1/§5.2): consensus floor on the difficulty of
    // the model a block may be MINED with.
''', '''    // NOET model switch (consensus/params.h ModelForHeight): exactly one model per height.
    const std::string required_model{Params().GetConsensus().ModelForHeight(pindex->nHeight)};
    if (!required_model.empty() && block.pow.model_identifier != required_model) {
        return state.Invalid(BlockValidationResult::BLOCK_CONSENSUS, "bad-model-for-height",
                             strprintf("%s: a block at height %d must carry model %s, not %s",
                                       call_path, pindex->nHeight, required_model, block.pow.model_identifier));
    }

    // INERT LEVER (ASERT_CHANGE.md §5.1/§5.2): consensus floor on the difficulty of
    // the model a block may be MINED with.
''')
    # With two Registered models upstream's sole-model protection lapses, and model
    # challenges are not verified (the check is commented out), so anyone could ban
    # the model a height requires and stop the chain. Both scheduled models keep it.
    return replace_once(text, '''        if (!consensusParams.IsProofV4Active(pindexNew->nHeight)) return false;
        const bool protected_now = count_registered_models() <= 1;
''', '''        if (!consensusParams.IsProofV4Active(pindexNew->nHeight)) return false;
        if (consensusParams.ModelSwitchScheduled() &&
            (model_hash == HashSHA256(consensusParams.DefaultModelName, consensusParams.DefaultModelCommit) ||
             model_hash == HashSHA256(consensusParams.SwitchModelName, consensusParams.SwitchModelCommit))) {
            LogPrintf("[ModelDB] challenge-scheduled-model-protected: model %s is a NOET consensus model; "
                      "resolving the challenge as failed instead of banning\\n", model_hash.ToString());
            return true;
        }
        const bool protected_now = count_registered_models() <= 1;
''')


def switch_miner(text):
    """Block assembly picks the model of the height it builds."""
    return replace_once(text, '''                const auto& cons = chainparams.GetConsensus();
                if (!cons.DefaultModelName.empty() && !cons.DefaultModelCommit.empty()) {
                    pblock->pow.model_identifier = cons.DefaultModelName + "@" + cons.DefaultModelCommit;
                }
''', '''                const auto& cons = chainparams.GetConsensus();
                const std::string scheduled{cons.ModelForHeight(nHeight)};  // NOET model switch
                if (!scheduled.empty()) {
                    pblock->pow.model_identifier = scheduled;
                } else if (!cons.DefaultModelName.empty() && !cons.DefaultModelCommit.empty()) {
                    pblock->pow.model_identifier = cons.DefaultModelName + "@" + cons.DefaultModelCommit;
                }
''')


def switch_modeldb(text):
    """The switch model is registered from genesis, like the genesis model."""
    return replace_once(text, '''    RefreshModelWorkFloor();
    LogPrintf("[ModelDB] Initialized database''', '''    // NOET model switch: register the scheduled model like the genesis model (also
    // in a database created before the switch was scheduled). Validation admits
    // each model only at its own heights (consensus/params.h ModelForHeight).
    if (consensusParams.ModelSwitchScheduled()) {
        const uint256 switch_hash = HashSHA256(consensusParams.SwitchModelName, consensusParams.SwitchModelCommit);
        if (!Exists(switch_hash)) {
            ModelRecord switch_record;
            switch_record.metadata.model_name = consensusParams.SwitchModelName;
            switch_record.metadata.model_commit = consensusParams.SwitchModelCommit;
            switch_record.metadata.difficulty = static_cast<int64_t>(consensusParams.ModelDifficultyNormalizer);
            switch_record.status = ModelRegistrationStatus::Registered;
            switch_record.deposit_block_hash = consensusParams.hashGenesisBlock;
            switch_record.deposit_block_height = 0;
            switch_record.commit_block_hash = consensusParams.hashGenesisBlock;
            switch_record.commit_block_height = 0;
            switch_record.deposit_amount = 0;
            switch_record.verification_code = 0;
            if (WriteModel(switch_hash, switch_record)) {
                LogPrintf("[ModelDB] NOET switch model %s@%s registered.\\n",
                          consensusParams.SwitchModelName, consensusParams.SwitchModelCommit);
            } else {
                LogPrintf("[ModelDB] Failed to write the NOET switch model.\\n");
            }
        }
    }
    RefreshModelWorkFloor();
    LogPrintf("[ModelDB] Initialized database''')


def switch_relay(text):
    """A block rebuilt from a compact relay gets the model of its height."""
    return replace_once(text, '''    if (block.pow.model_identifier.empty()) {
        const auto& cons = m_chainman.GetParams().GetConsensus();
        if (!cons.DefaultModelName.empty() && !cons.DefaultModelCommit.empty()) {
            block.pow.model_identifier = cons.DefaultModelName + "@" + cons.DefaultModelCommit;
        }
    }
''', '''    if (block.pow.model_identifier.empty()) {
        const auto& cons = m_chainman.GetParams().GetConsensus();
        const std::string scheduled{height >= 0 ? cons.ModelForHeight(height) : std::string{}};  // NOET model switch
        if (!scheduled.empty()) {
            block.pow.model_identifier = scheduled;
        } else if (!cons.DefaultModelName.empty() && !cons.DefaultModelCommit.empty()) {
            block.pow.model_identifier = cons.DefaultModelName + "@" + cons.DefaultModelCommit;
        }
    }
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
    switch = applied_switch(net, test)
    result = {
        'src/kernel/chainparams.cpp': chainparams(sources['src/kernel/chainparams.cpp'], test, net),
        'src/init.cpp': init(sources['src/init.cpp'], test, net),
        'src/consensus/amount.h': amount(sources['src/consensus/amount.h'], net),
        'src/consensus/params.h': params(sources['src/consensus/params.h'], switch),
        'src/validation.cpp': validation(sources['src/validation.cpp'], net),
        'src/node/miner.cpp': miner(sources['src/node/miner.cpp'], net),
        f'src/noet_{n}.h': support(net),
        f'src/noet_{n}_allocations.h': allocation_header(outputs(net['keys']), n),
        # A network may set its own release rate; the shared tokenomics otherwise.
        f'src/noet_{n}_policy.h': (ROOT / 'economics/candidate/policy.h').read_text()
        if net['releasePercent'] == RELEASE_PERCENT else policy_text(net['releasePercent']),
    }
    if switch:
        result['src/validation.cpp'] = switch_validation(result['src/validation.cpp'])
        result['src/node/miner.cpp'] = switch_miner(result['src/node/miner.cpp'])
        result['src/modeldb.cpp'] = switch_modeldb(sources['src/modeldb.cpp'])
        result['src/net_processing.cpp'] = switch_relay(sources['src/net_processing.cpp'])
    return result


def profile(sources, result, test=False, net=TESTNET):
    files = {}
    for path, text in result.items():
        before = sources.get(path)
        files[path] = {'beforeSha256': hashlib.sha256(before.encode()).hexdigest() if before is not None else None,
                       'afterSha256': hashlib.sha256(text.encode()).hexdigest()}
    switch = applied_switch(net, test)
    extra = {'modelSwitch': {'model': switch['model'] + '@' + switch['revision'],
                             'height': '-modelswitchheight' if test else switch['height'],
                             'targetScale': list(switch['targetScale'])}} if switch else {}
    return {'format': net['profile'], 'variant': 'test' if test else 'release', 'model': MODEL + '@' + REVISION,
            **extra, 'genesisTime': net['genesisTime'], 'addressPrefix': net['hrp'], 'p2pPort': net['port'],
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
