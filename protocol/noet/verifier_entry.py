"""Start the development verifier only after approving its local model snapshot."""
import os
import ipaddress
import runpy
import re
import sys
from .model_runtime import checked_model_directory

_STARTUP = object()

def require_challenge_binding():
    """No legacy/shadow VDF challenge mode in a new guarded deployment."""
    enabled = os.environ.get('POW_VDF_CHALLENGE_BIND_ENFORCE', '').strip().lower()
    activation = os.environ.get('POW_VDF_CHALLENGE_BIND_ACTIVATION_TIME', '').strip()
    if enabled not in ('', '1', 'true', 'yes') or activation not in ('', '0'):
        raise ValueError('The guarded verifier requires immediate VDF challenge binding')
    os.environ['POW_VDF_CHALLENGE_BIND_ENFORCE'] = '1'
    os.environ['POW_VDF_CHALLENGE_BIND_ACTIVATION_TIME'] = '0'


def require_prompt_calibration(model_identifier=_STARTUP, precision=_STARTUP):
    """Require explicit calibration scope; never infer approval from replay success.

    This checks deployment declarations, not their scientific validity. The
    release review must establish calibration evidence before supplying them.
    Called again per proof so a different precision cannot fall back to shadow.
    """
    fixed = {
        'NOET_REQUIRE_PROMPT_CALIBRATION': '1',
        'POW_PROMPT_GIBBERISH_GATE': 'enforce',
        'POW_PROMPT_GIBBERISH_ACTIVATION_TIME': '0',
        'POW_PROMPT_GIBBERISH_BITS': '14.0',
        'POW_PROMPT_GIBBERISH_MIN_TRANSITIONS': '32',
        'POW_PROMPT_GIBBERISH_TAIL': '256',
    }
    for key, value in fixed.items():
        if os.environ.get(key, value).strip() != value:
            raise ValueError('Guarded prompt calibration requires ' + key + '=' + value)
    model = os.environ.get('MODEL_NAME', '')
    revision = os.environ.get('MODEL_COMMIT', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Prompt calibration requires an exact model and immutable revision')
    expected = model + '@' + revision
    raw = os.environ.get('POW_PROMPT_GIBBERISH_CALIBRATED', '')
    entries = [entry.strip() for entry in raw.split(',')]
    allowed = {expected + ':' + p for p in ('bf16', 'fp16', 'fp32', 'fp8', 'int8')}
    if not entries or len(entries) != len(set(entries)) or any(entry not in allowed for entry in entries):
        raise ValueError('Declare reviewed prompt calibration as exact model@revision:precision entries; no wildcard or implicit defaults')
    if model_identifier is not _STARTUP or precision is not _STARTUP:
        if model_identifier != expected or not isinstance(precision, str) or expected + ':' + precision not in entries:
            raise ValueError('No reviewed prompt calibration for this proof model and precision')
    os.environ.update(fixed)


def main():
    if os.environ.get('TEST_MODE', '').strip().lower() not in ('', '0', 'false', 'no'):
        raise ValueError('Synthetic proof verification is disabled in the guarded verifier')
    os.environ['TEST_MODE'] = 'false'
    if os.environ.get('REMOTE_VERIFY_ENABLED', '').strip().lower() not in ('', '0', 'false', 'no') or os.environ.get('REMOTE_VERIFY_BASE_URL'):
        raise ValueError('Remote verdict delegation is disabled')
    os.environ['REMOTE_VERIFY_ENABLED'] = 'false'
    os.environ.pop('REMOTE_VERIFY_API_KEY', None)
    require_challenge_binding()
    require_prompt_calibration()
    for endpoint in ('ZMQ_VERIFY_PUSH_HOST', 'NOET_VERIFIER_BIND'):
        host = os.environ.setdefault(endpoint, '127.0.0.1')
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_loopback:
            raise ValueError('Plaintext verifier endpoints must be loopback; use the authenticated TLS relay')
    # Set these before importing any Transformers or Hub modules.
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE',
                 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
        os.environ[name] = '1'
    for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HUGGINGFACE_HUB_TOKEN'):
        os.environ.pop(name, None)
    checked_model_directory(os.environ['MODEL_NAME'], os.environ['MODEL_COMMIT'])
    if re.fullmatch(r'[0-9a-fA-F]{64}', os.environ.get('VALIDATOR_SHARED_SECRET', '')) is None:
        raise ValueError('VALIDATOR_SHARED_SECRET must be a separately generated 32-byte hex secret')
    sys.path.insert(0, '/app/src')
    runpy.run_path('/app/src/main.py', run_name='__main__')


if __name__ == '__main__':
    main()
