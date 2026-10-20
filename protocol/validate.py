#!/usr/bin/env python3
"""Repeatable local acceptance entry point. Never provisions paid infrastructure."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mysql', action='store_true', help='Run web tests with a disposable Docker MySQL database')
    parser.add_argument('--native', action='store_true', help='Run disposable native Docker partition/crash/recovery checks')
    parser.add_argument('--native-vdf', action='store_true', help='Build pinned native VDF acceptance with Clang, GMP and sanitizers')
    parser.add_argument('--gmp-prefix', type=Path, help='Optional GMP installation prefix for native VDF acceptance')
    args = parser.parse_args()
    if not (ROOT / 'protocol/.upstream/services/miner-api/src/components/constants.py').exists():
        parser.error('Prepare pinned sources with protocol/bootstrap.py first; miner checks must not be silently skipped.')
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'protocol')}
    checks = [
        ('Protocol', [sys.executable, '-m', 'unittest', 'discover', '-s', 'protocol/tests', '-v'], ROOT),
        ('Economics', [sys.executable, '-m', 'unittest', 'discover', '-s', 'protocol/economics'], ROOT),
        ('Wallet companion', ['node', '--test', 'wallets/tests/companion.test.mjs', 'wallets/tests/native.test.mjs', 'wallets/tests/native-ui.test.mjs'], ROOT),
        ('TypeScript', ['npm', 'run', 'check'], ROOT / 'web'),
        ('Website tests', ['npm', 'run', 'test:mysql' if args.mysql else 'test'], ROOT / 'web'),
        ('Website build', ['npm', 'run', 'build'], ROOT / 'web'),
    ]
    if args.native_vdf:
        import tempfile
        report = Path(tempfile.mkdtemp(prefix='noet-vdf-report-')) / 'acceptance.json'
        command = [sys.executable, 'protocol/tests/native_vdf_smoke.py', '--out', str(report)]
        if args.gmp_prefix:
            command += ['--include-dir', str(args.gmp_prefix / 'include'), '--library-dir', str(args.gmp_prefix / 'lib')]
        checks.append(('Native VDF generation and rejection', command, ROOT))
    if args.native:
        import tempfile
        work_report = Path(tempfile.mkdtemp(prefix='noet-work-report-')) / 'acceptance.json'
        checks.append(('Native work issuance', [sys.executable, 'protocol/tests/mining_work_smoke.py', '--out', str(work_report)], ROOT))
        checks.append(('Native recovery', [sys.executable, 'protocol/tests/node_smoke.py', '--reindex', '--partition'], ROOT))
        checks.append(('Native time-locked multisignature script', [sys.executable, 'protocol/tests/vesting_smoke.py'], ROOT))
    for name, command, cwd in checks:
        print('CHECK: ' + name, flush=True)
        subprocess.run(command, cwd=cwd, env=env, check=True)
    print('Selected acceptance checks passed. This is not production-mining certification.')
    if not args.mysql:
        print('Database integration was not required by this invocation; use --mysql for complete web acceptance.')


if __name__ == '__main__':
    main()
