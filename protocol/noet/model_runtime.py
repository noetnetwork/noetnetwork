"""Fail-closed local artifact gate for the development mining backend."""
import json
import os
from pathlib import Path
import stat
from .model_artifacts import MAX_MANIFEST, unique_object, validate, verify


def require_snapshot_protection(root, files):
    """Protect artifacts against the loader process, with trusted host administration.

    Read-only mounts remain the default. Container hosts without mount support
    can explicitly choose a root-owned tree and run the loader without root or
    capabilities. Every ancestor and file is checked, including ACL write access.
    """
    mode = os.environ.get('NOET_MODEL_PROTECTION', 'mount')
    if mode == 'mount':
        for path in [root, *(root / row['path'] for row in files)]:
            if not os.statvfs(path).f_flag & os.ST_RDONLY:
                raise ValueError('Every model file must be mounted read-only')
        return
    if mode != 'root-owned':
        raise ValueError('Unsupported model protection mode')
    if os.geteuid() == 0 or os.getuid() != os.geteuid():
        raise ValueError('Root-owned model protection requires an unprivileged loader')
    # A UID alone is insufficient: retained DAC/ownership capabilities could
    # let the worker modify files. Refuse all effective or permitted capabilities.
    status = Path('/proc/self/status').read_text()
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    if (fields['NoNewPrivs'].strip() != '1'
            or [int(uid) for uid in fields['Uid'].split()] != [os.geteuid()] * 4):
        raise ValueError('Model loader must permanently drop privileges')
    for field in ('CapEff', 'CapPrm', 'CapAmb'):
        if int(fields[field].strip(), 16) != 0:
            raise ValueError('Model loader must have no process capabilities')
    directories = {root, *root.parents}
    paths = []
    for row in files:
        path = root / row['path']
        paths.append(path)
        directories.update(path.parents)
    for path in directories | set(paths):
        entry = path.lstat()
        expected = stat.S_ISDIR(entry.st_mode) if path in directories else stat.S_ISREG(entry.st_mode)
        if (not expected or entry.st_uid != 0 or entry.st_mode & 0o022
                or os.access(path, os.W_OK, effective_ids=True)):
            raise ValueError('Model path is not protected from the loader: ' + str(path))


def required_key(name):
    value = os.environ.get(name, '')
    if len(value) < 32 or len(value) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError(name + ' must be a separately generated ASCII secret of 32 to 512 characters')
    return value


def _reviewed_snapshot(suffix):
    root = Path(os.environ['NOET_MODEL_ROOT' + suffix])
    manifest_path = Path(os.environ['NOET_MODEL_MANIFEST' + suffix])
    if not root.is_absolute() or root.is_symlink() or not manifest_path.is_absolute():
        raise ValueError('Use absolute model and manifest paths without a model-root symlink')
    fd = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Model manifest must be a regular file')
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Model manifest exceeds the supported size')
    return root, raw, validate(json.loads(raw, object_pairs_hook=unique_object))


def checked_model_directory(model, revision):
    """The verified snapshot of exactly this model. A deployment reviews one
    snapshot (NOET_MODEL_ROOT, _MANIFEST, _MANIFEST_SHA256) and, across a
    scheduled model switch (design/MODEL-SWITCH.md), a second with the same
    names ending in _2, so one verifier replays both sides of the switch."""
    suffixes = ('', '_2') if 'NOET_MODEL_ROOT_2' in os.environ else ('',)
    for suffix in suffixes:
        root, raw, data = _reviewed_snapshot(suffix)
        if data['model'] != model or data['revision'] != revision:
            continue
        require_snapshot_protection(root, data['files'])
        report = verify(root, raw, os.environ['NOET_MODEL_MANIFEST_SHA256' + suffix])
        if not report['filesVerified']:
            raise ValueError('Model artifact verification failed')
        return str(root)
    raise ValueError('Requested model is outside the reviewed deployment')


def backend_environment(api_key):
    env = dict(os.environ)
    env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                'HF_DATASETS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'VLLM_NO_USAGE_STATS': '1', 'DO_NOT_TRACK': '1',
                'VLLM_API_KEY': api_key, 'VLLM_ENABLE_POW': '1'})
    # Model downloads must not receive account tokens, and the inference child
    # does not need the supervisor's administrative credential.
    for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HUGGINGFACE_HUB_TOKEN', 'VLLM_ADMIN_KEY', 'API_KEY'):
        env.pop(key, None)
    return env


def approved_pretrained(loader, model, *, revision, **kwargs):
    """Load a Transformers object only from the verified deployment snapshot."""
    root = checked_model_directory(model, revision)
    if kwargs.get('trust_remote_code') not in (None, False):
        raise ValueError('Remote model code is not supported')
    if kwargs.get('use_safetensors') is False:
        raise ValueError('Only safetensors weights are supported')
    for key in ('token', 'use_auth_token', 'subfolder', 'code_revision', 'force_download', 'proxies'):
        if kwargs.get(key):
            raise ValueError('Model loader override is not supported: ' + key)
        kwargs.pop(key, None)
    kwargs.update(local_files_only=True, trust_remote_code=False)
    return loader.from_pretrained(root, **kwargs)
