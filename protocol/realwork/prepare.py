#!/usr/bin/env python3
"""Build inputs for a disposable real-work integration network, never mainnet.

This is a separate source overlay. The original laboratory network and its
historical evidence are unchanged. No calibration or verifier rule is relaxed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from source_bundle import pack, git

MODEL = 'Qwen/Qwen3-0.6B'
REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
PROFILE = 'noet-private-work-v1'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Expected exactly one source anchor: ' + old[:100])
    return text.replace(old, new, 1)


def overlay(chain, init, model=MODEL, revision=REVISION):
    start = chain.index('class CTensorRegParams')
    end = chain.index('\n};', start) + 3
    block = chain[start:end]
    for old, new in (
        ('pchMessageStart[1] = 0x4f;', 'pchMessageStart[1] = 0x57;'),
        ('pchMessageStart[2] = 0x45;', 'pchMessageStart[2] = 0x52;'),
        ('pchMessageStart[3] = 0x54;', 'pchMessageStart[3] = 0x4b;'),
        ('nDefaultPort = 29441;', 'nDefaultPort = 30441;'),
        ('CreateGenesisBlock(1791676800, 2,', 'CreateGenesisBlock(1791676860, 2,'),
        ('consensus.DefaultModelName = "testModel";', f'consensus.DefaultModelName = "{model}";'),
        ('consensus.DefaultModelCommit = "testModelCommit";', f'consensus.DefaultModelCommit = "{revision}";'),
        ('genesis.pow.model_identifier = "testModel@testModelCommit";', 'genesis.pow.model_identifier = consensus.DefaultModelName + "@" + consensus.DefaultModelCommit;'),
        ('m_is_mockable_chain = true;', 'm_is_mockable_chain = false;\n        consensus.external_api = true;'),
        ('bech32_hrp = "nlab";', 'bech32_hrp = "nwrk";'),
        ('std::vector<unsigned char>(1,0x35)', 'std::vector<unsigned char>(1,0x36)'),
        ('std::vector<unsigned char>(1,0x75)', 'std::vector<unsigned char>(1,0x76)'),
        ('std::vector<unsigned char>(1,0xB5)', 'std::vector<unsigned char>(1,0xB6)'),
        ('{0x04, 0x72, 0x4e, 0x50}', '{0x04, 0x72, 0x57, 0x50}'),
        ('{0x04, 0x72, 0x4e, 0x58}', '{0x04, 0x72, 0x57, 0x58}'),
    ):
        block = replace_once(block, old, new)
    for field, value in {'V3ActivationHeight':1, 'AntiParrotHeight':1,
        'PadSlotZeroHeight':1, 'StepBindHeight':1, 'StepBindGraceEndTime':0,
        'ProofV4Height':1, 'V3PenaltyHeight':2, 'V3SunsetHeight':3,
        'ProofHistoryCollectHeight':1, 'ProofHistoryEnforceHeight':2,
        'nEnforcedProofWindowSize':256}.items():
        pattern = r'consensus\.' + field + r' = [^;]+;'
        block, count = re.subn(pattern, f'consensus.{field} = {value};', block)
        if count != 1: raise ValueError('Unexpected activation field: '+field)
    chain = chain[:start] + block + chain[end:]
    anchor = '    const std::string api_mode = to_lower(args.GetArg("-validationapi", "real"));'
    init = replace_once(init, anchor, anchor + '''
    // This binary is exclusively for the separately identified private work network.
    // Reject unsupported modes instead of redirecting to any public verifier.
    if (chainparams.GetChainType() != ChainType::TENSOR_REG || api_mode != "real" ||
        !args.GetBoolArg("-useextapi", true)) {
        return ChainstateLoadResult{ChainstateLoadStatus::FAILURE_FATAL,
            Untranslated("NOET private work requires its own chain, real validation and external verification")};
    }
''')
    return chain, init


def prepare(builder, output, model=MODEL, revision=REVISION):
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}', builder):
        raise ValueError('Pin the reviewed native compiler image by digest')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Use organization/model and a full 40-character revision')
    source = ROOT/'.upstream/services/core-node/bcore'
    lock = json.loads((ROOT/'upstream.lock.json').read_text())
    commit = lock['components']['services/core-node/bcore']
    patch = ROOT/'patches/noet-lab.patch'
    if git(source, 'rev-parse', 'HEAD').decode().strip() != commit:
        raise ValueError('Native source commit differs')
    if git(source, 'diff', '--no-ext-diff', '--binary', 'HEAD').strip() != patch.read_bytes().strip():
        raise ValueError('Native source differs from the reviewed laboratory patch')
    paths = ('src/kernel/chainparams.cpp', 'src/init.cpp')
    before = [(source/p).read_text() for p in paths]
    after = overlay(*before, model, revision)
    with tempfile.TemporaryDirectory(prefix='noet-private-work-') as folder:
        context = Path(folder)
        files = {}
        for path, original, updated in zip(paths, before, after):
            target = context/path; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(updated)
            files[path] = {'beforeSha256':hashlib.sha256(original.encode()).hexdigest(),
                           'afterSha256':hashlib.sha256(updated.encode()).hexdigest()}
        record = {'format':PROFILE, 'model':model+'@'+revision, 'builder':builder,
            'sourceCommit':commit, 'labPatchSha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
            'files':files, 'productionReady':False, 'registeredDifficulty':1000000,
            'purpose':'Disposable real-work integration; inherited test economics; unapproved research model'}
        (context/'profile.json').write_text(json.dumps(record, indent=2)+'\n')
        checks = '\n'.join('RUN echo "'+files[p]['beforeSha256']+'  /src/bcore/'+p+'" | sha256sum -c -' for p in paths)
        (context/'Dockerfile').write_text('FROM '+builder+'\n'+checks+'''
COPY src/ /src/bcore/src/
RUN touch /src/bcore/src/kernel/chainparams.cpp /src/bcore/src/init.cpp && cmake --build /src/bcore/build --target bitcoind bitcoin-cli bitcoin-wallet -j3
COPY profile.json /opt/noet/private-work-profile.json
WORKDIR /tmp
ENTRYPOINT ["/src/bcore/build/bin/bitcoind", "-chain=tensor-reg", "-dnsseed=0", "-fixedseeds=0", "-listenonion=0", "-discover=0", "-printtoconsole=1"]
''')
        return pack(context, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--builder', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--revision', default=REVISION)
    args = parser.parse_args()
    print(prepare(args.builder, args.out, args.model, args.revision))
