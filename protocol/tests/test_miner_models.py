"""Registry selection policy against the actual pinned miner configuration."""
import unittest
import test_miner_config as configuration

CONSTANTS = configuration.CONSTANTS
LOAD_CONFIG = configuration.LOAD_CONFIG

SETUP = LOAD_CONFIG + """
from unittest.mock import Mock
client = Mock()
record = {'model_name':'NOET/example', 'model_commit':'a'*40, 'model_hash':'b'*64, 'difficulty':123}
client.get_models_by_name.return_value = [record]
client.get_model_by_hash.return_value = record
by_name = config['get_model_config']
by_hash = config['get_model_config_by_hash']
"""


@unittest.skipUnless(CONSTANTS.exists(), 'Run protocol/bootstrap.py first')
class MinerModelTests(unittest.TestCase):
    def run_case(self, script):
        result = configuration.MinerConfigurationTests().load_config(script=SETUP + script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_registered_identity_preserved(self):
        self.run_case("""
model = by_name('NOET/example', client)
assert model.model_name == record['model_name']
assert model.model_commit == record['model_commit']
assert model.model_hash == record['model_hash']
assert model.difficulty == 123
assert by_hash('b'*64, client) == model
""")

    def test_missing_registry_never_returns_static_model(self):
        self.run_case("""
for name in ('unknown', 'Qwen/Qwen3-8B'):
    for registry in (None, client):
        client.get_models_by_name.return_value = []
        try:
            by_name(name, registry)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Unexpected fallback model')
assert by_hash('0'*64) is None
client.get_model_by_hash.return_value = None
assert by_hash('0'*64, client) is None
""")

    def test_ambiguous_revisions_require_pin(self):
        self.run_case("""
client.get_models_by_name.return_value = [record, dict(record, model_commit='c'*40, block_height=999)]
try:
    by_name('NOET/example', client)
except RuntimeError as error:
    assert 'pin MODEL_COMMIT' in str(error)
else:
    raise AssertionError('Automatically selected a revision')
""")

    def test_mismatched_and_incomplete_identity_rejected(self):
        self.run_case("""
for key in ('model_name','model_commit','model_hash'):
    for value in ('', None, ' '):
        bad = dict(record, **{key:value})
        client.get_models_by_name.return_value = [bad]
        client.get_model_by_hash.return_value = bad
        for call in (lambda: by_name('NOET/example', client), lambda: by_hash('b'*64, client)):
            try:
                call()
            except RuntimeError:
                pass
            else:
                raise AssertionError('Incomplete identity accepted')
client.get_models_by_name.return_value = [record]
client.get_model_by_hash.return_value = record
for call in (lambda: by_name('other/model', client), lambda: by_hash('c'*64, client)):
    try:
        call()
    except RuntimeError:
        pass
    else:
        raise AssertionError('Mismatched identity accepted')
""")

    def test_mutable_revisions_and_malformed_registry_hashes_rejected(self):
        self.run_case("""
for key, values in (
    ('model_commit', ('main','v1','a'*7,'A'*40,'a'*41)),
    ('model_hash', ('b'*63,'B'*64,'b'*65)),
    ('model_name', ('unscoped','https://example.com/model','org/model/extra')),
):
    for value in values:
        client.get_models_by_name.return_value = [dict(record, **{key:value})]
        try:
            by_name(value if key == 'model_name' else 'NOET/example', client)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Mutable or malformed identity accepted')
""")

    def test_registry_error_does_not_trigger_fallback(self):
        self.run_case("""
client.get_models_by_name.side_effect = RuntimeError('unavailable')
try:
    by_name('Qwen/Qwen3-8B', client)
except RuntimeError as error:
    assert str(error) == 'unavailable'
else:
    raise AssertionError('Registry outage was hidden')
""")

    def test_pinned_revision_requires_exact_registry_identity(self):
        self.run_case("""
pinned = config['get_pinned_model_config']
client.get_model_by_name_and_commit.return_value = record
assert pinned('NOET/example', 'a'*40, client).model_commit == 'a'*40
for bad in (None, dict(record, model_commit='c'*40), dict(record, model_name='other/model'), dict(record, model_hash='')):
    client.get_model_by_name_and_commit.return_value = bad
    try:
        pinned('NOET/example', 'a'*40, client)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Mismatched pin accepted')
""")

    def test_request_injection_rejects_bad_registry_without_rewriting_model(self):
        self.run_case("""
import ast, logging, threading
# Execute the actual injection method without importing the GPU/web stack.
source = ast.parse((root / 'services/miner-api/src/components/proxy.py').read_text())
method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == '_inject_pow_data')
namespace = {'constants':types.SimpleNamespace(**config), 'logger':logging.getLogger('test')}
exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual-injection-method>', 'exec'), namespace)
worker = types.SimpleNamespace(_broker_inference_only=False, _switch_in_progress=False,
    _switch_state_lock=threading.Lock(), _is_mining_paused=lambda:False, model_client=client)
client.models_by_name = {}
namespace['constants'].GENESIS_GENERATOR = False
for pinned, records in ((False, []), (False, [record,record]), (False, [dict(record, model_name='other/model')]), (True, None), (True, dict(record, model_commit='c'*40))):
    worker._current_model_selection = lambda: ('NOET/example','a'*40,pinned)
    client.get_models_by_name.return_value = records
    client.get_model_by_name_and_commit.return_value = records
    data = {'model':'NOET/example', 'prompt':'test'}
    try:
        namespace['_inject_pow_data'](worker, data)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Unsafe model selection reached injection')
    assert data == {'model':'NOET/example', 'prompt':'test'}
""")
