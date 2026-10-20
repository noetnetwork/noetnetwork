"""Before a candidate-model test downloads anything: does this rented host have
the GPU memory and disk the model needs, and which GPU runs which part?

The producer holds the bf16 GGUF (about the size of the bf16 weights); the
verifier replays in fp32, twice the weights. With two GPUs that fit, the
producer gets GPU 0 and the verifier GPU 1; otherwise both share GPU 0. The
headroom figures are estimates until a test records peak memory
(gpu-memory.csv). Prints "<producer GPU> <verifier GPU>"; exits 1 with what
the model needs when the host is too small. Runs as root with system Python.
"""
import argparse
import json
import shutil
import subprocess
import sys

# GiB, as nvidia-smi and GPU names count: an "80 GB" card has 80 GiB.
GB = 2**30
PRODUCER_HEADROOM = 6 * GB   # KV cache, compute buffers, CUDA context
VERIFIER_HEADROOM = 10 * GB  # fp32 logits over the vocabulary for several replays, CUDA context
DISK_HEADROOM = 10 * GB


def needs(weights):
    # Disk: the verifier's snapshot and the GGUF, plus a second download and
    # conversion that must reproduce the GGUF byte for byte.
    return {'producer': weights + PRODUCER_HEADROOM, 'verifier': 2 * weights + VERIFIER_HEADROOM,
            'disk': 4 * weights + DISK_HEADROOM}


def plan(weights, gpus, free_disk, converted):
    """gpus: total memory of each GPU in bytes, by index. Returns (producer, verifier)."""
    need = needs(weights)
    if not converted and free_disk < need['disk']:
        raise ValueError(f'needs {need["disk"] / GB:.1f} GB free disk for download and conversion; '
                         f'{free_disk / GB:.1f} GB free')
    if len(gpus) >= 2 and gpus[0] >= need['producer'] and gpus[1] >= need['verifier']:
        return 0, 1
    if gpus and gpus[0] >= need['producer'] + need['verifier']:
        return 0, 0
    found = ', '.join(f'{memory / GB:.1f}' for memory in gpus) or 'none'
    raise ValueError(f'needs one GPU with {(need["producer"] + need["verifier"]) / GB:.1f} GB, or GPU 0 with '
                     f'{need["producer"] / GB:.1f} GB (producer) and GPU 1 with {need["verifier"] / GB:.1f} GB '
                     f'(verifier); found GPUs with {found} GB')


def gpu_memory():
    rows = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.total', '--format=csv,noheader,nounits'],
                          check=True, capture_output=True, text=True, timeout=60).stdout.split('\n')
    memory = {}
    for row in filter(None, (r.strip() for r in rows)):
        index, mib = (int(value) for value in row.split(','))
        memory[index] = mib * 1024 * 1024
    return [memory[index] for index in sorted(memory)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, help='candidate model manifest')
    parser.add_argument('--disk', required=True, help='folder the model is downloaded and converted in')
    parser.add_argument('--converted', action='store_true', help='the GGUF is already in place')
    parser.add_argument('--out', required=True, help='write the plan here as JSON')
    args = parser.parse_args()
    with open(args.manifest) as stream:
        weights = sum(row['bytes'] for row in json.load(stream)['files'])
    gpus = gpu_memory()
    free_disk = shutil.disk_usage(args.disk).free
    record = {'weightsBytes': weights, 'gpuMemoryBytes': gpus, 'freeDiskBytes': free_disk,
              'converted': args.converted, 'needs': needs(weights)}
    try:
        record['producerGpu'], record['verifierGpu'] = plan(weights, gpus, free_disk, args.converted)
    except ValueError as error:
        record['error'] = str(error)
        print('This host is too small for the model: ' + str(error), file=sys.stderr)
    with open(args.out, 'w') as stream:
        stream.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    if 'error' in record:
        return 1
    print(record['producerGpu'], record['verifierGpu'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
