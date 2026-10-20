"""Node overlay: every mining work unit carries its coinbase and merkle branch.

A miner that leases work through a gateway otherwise has only the 76-byte
header prefix and must trust the gateway about where the reward goes. With
the coinbase (no-witness serialization, whose double SHA-256 is the txid) and
the coinbase's merkle branch (internal byte order; the coinbase is leaf 0, so
every sibling hashes on the right), the miner recomputes the merkle root, checks
it against the header prefix and reads the payout outputs before mining.
RPC output only; consensus and block contents are unchanged.
"""
FILE = 'src/rpc/mining.cpp'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Payout-proof overlay anchor not found exactly once: {old[:70]!r}')
    return text.replace(old, new)


FIELDS_DOC = '''            {RPCResult::Type::STR_HEX, "coinbase_tx", "NOET: this unit's coinbase, serialized without witness (its double SHA-256 is the txid)"},
            {RPCResult::Type::ARR, "coinbase_merkle_branch", "NOET: right-hand siblings from the coinbase (leaf 0) to the merkle root, internal byte order",
                {{RPCResult::Type::STR_HEX, "", "sibling hash"}}},
'''


def overlay(text):
    text = replace_once(text, '#include <core_io.h>\n', '#include <core_io.h>\n#include <streams.h>\n')
    text = replace_once(
        text,
        '    std::vector<std::pair<uint32_t, std::string>> units; // (req_id, header_prefix)\n',
        '    std::vector<std::pair<uint32_t, std::string>> units; // (req_id, header_prefix)\n'
        '    // NOET: per unit, the coinbase (no witness, hex) and its merkle branch (internal order).\n'
        '    std::vector<std::pair<std::string, std::vector<std::string>>> payouts;\n')
    text = replace_once(
        text,
        '''        out.units.emplace_back(issue.req_id,
            EncodeHeaderPrefixFields(tpl->tmpl->block.nVersion, tpl->parent_hash, merkle_root,
                                     tpl->tmpl->block.nTime, tpl->nBits));
''',
        '''        out.units.emplace_back(issue.req_id,
            EncodeHeaderPrefixFields(tpl->tmpl->block.nVersion, tpl->parent_hash, merkle_root,
                                     tpl->tmpl->block.nTime, tpl->nBits));
        {
            DataStream coinbase_bytes;
            coinbase_bytes << TX_NO_WITNESS(*coinbase);
            std::vector<std::string> branch;
            for (const uint256& sibling : tpl->coinbase_branch) branch.push_back(HexStr(sibling));
            out.payouts.emplace_back(HexStr(coinbase_bytes), std::move(branch));
        }
''')
    # create_mining_work_unit: result fields and their documentation.
    text = replace_once(
        text,
        '''    result.pushKV("tip_hash", batch.parent_hash.ToString());
    // StepBind activation''',
        '''    result.pushKV("tip_hash", batch.parent_hash.ToString());
    {
        result.pushKV("coinbase_tx", batch.payouts[0].first);
        UniValue branch(UniValue::VARR);
        for (const std::string& sibling : batch.payouts[0].second) branch.push_back(sibling);
        result.pushKV("coinbase_merkle_branch", std::move(branch));
    }
    // StepBind activation''')
    single_doc = '''            {RPCResult::Type::STR_HEX, "tip_hash", "hash of the block this work unit builds on (hashPrevBlock). The broker keys stale-tip lease supersession on this: when getmininginfo.tip_hash advances past it, the lease is dead."},
'''
    text = replace_once(text, single_doc, single_doc + FIELDS_DOC)
    # create_mining_work_units_batch: per-unit fields and documentation.
    text = replace_once(
        text,
        '''    for (const auto& [req_id, header_prefix] : batch.units) {
        UniValue u(UniValue::VOBJ);
        u.pushKV("req_id", static_cast<uint64_t>(req_id));
        u.pushKV("header_prefix", header_prefix);
        units.push_back(std::move(u));
    }''',
        '''    for (size_t i = 0; i < batch.units.size(); ++i) {
        const auto& [req_id, header_prefix] = batch.units[i];
        UniValue u(UniValue::VOBJ);
        u.pushKV("req_id", static_cast<uint64_t>(req_id));
        u.pushKV("header_prefix", header_prefix);
        u.pushKV("coinbase_tx", batch.payouts[i].first);
        UniValue branch(UniValue::VARR);
        for (const std::string& sibling : batch.payouts[i].second) branch.push_back(sibling);
        u.pushKV("coinbase_merkle_branch", std::move(branch));
        units.push_back(std::move(u));
    }''')
    batch_doc = '''                    {RPCResult::Type::STR_HEX, "header_prefix", "76-byte serialised header without nNonce"},
'''
    text = replace_once(text, batch_doc, batch_doc + '\n'.join(
        '        ' + line if line else line for line in FIELDS_DOC.split('\n')))
    return text
