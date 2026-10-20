"""Link the response regression against the actual built engine libraries."""
import argparse
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    source, build = args.source.resolve(), args.build.resolve()
    commands = subprocess.check_output(['ninja', '-t', 'commands', 'llama-server'], cwd=build, text=True)
    link = shlex.split(commands.splitlines()[-1])
    # Retain the build's exact library order and platform dependencies. Nothing
    # from the command is interpreted by a shell.
    start = link.index('&&') + 1
    link = link[start:link.index('&&', start)]
    output = build / 'noet-response-test'
    command = [link[0], '-std=c++17', '-O0']
    for include in ('tools/server', 'common', 'include', 'ggml/include', 'vendor', 'vendor/cpp-httplib', 'tools/mtmd'):
        command += ['-I', str(source / include)]
    command += [str(Path(__file__).with_name('response_test.cpp')), '-o', str(output)]
    after_output = link.index('-o') + 2
    command += link[after_output:]
    subprocess.run(command, cwd=build, check=True)
    subprocess.run([str(output)], cwd=build, check=True)


if __name__ == '__main__':
    main()
