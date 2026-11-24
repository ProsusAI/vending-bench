"""The operator-and-worker runner against the real simulator, with a scripted model."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import run_team as team
os.environ['OPENROUTER_API_KEY'] = 'test-key-not-real'
out, mode = Path(sys.argv[1]), sys.argv[2]
sent = []

def call(i, name, arguments):
    return {'id': f'{name}-{i}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}

class Response:
    status_code = 200
    def __init__(self, body): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body

class API:
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def post(self, path, **kwargs):
        payload = kwargs['json']
        sent.append(payload)
        names = {t['function']['name'] for t in payload.get('tools', [])}
        last = payload['messages'][-1]
        if payload['model'] == 'test/worker':
            assert 'order_goods' not in names and 'restock_machine' in names
            if payload.get('tool_choice') == 'none' or last['role'] == 'tool':
                message = {'role': 'assistant', 'content': 'Checked AI-LOUNGE-SNACK: empty.'}
            else:
                message = {'role': 'assistant', 'tool_calls': [
                    call(len(sent), 'get_machine_inventory', {'machine_id': 'AI-LOUNGE-SNACK'})]}
        else:
            assert 'restock_machine' not in names and 'run_sub_agent' in names
            if mode == 'crash' and len(sent) == 4:
                raise RuntimeError('network went away')
            if len(sent) == 1:
                message = {'role': 'assistant', 'tool_calls': [
                    call(1, 'run_sub_agent', {'instructions': 'Check AI-LOUNGE-SNACK.'})]}
            elif len(sent) == 4:
                message = {'role': 'assistant', 'tool_calls': [
                    call(4, 'chat_with_sub_agent', {'message': 'What did you see?'}),
                    call(5, 'restock_machine', {'slot': 'A1', 'product_id': 'x', 'quantity': 1})]}
            else:
                message = {'role': 'assistant', 'tool_calls': [
                    call(f'{len(sent)}-{i}', 'wait_for_next_day', {}) for i in range(10)]}
        return Response({'id': f'gen-{len(sent)}', 'model': payload['model'],
                         'usage': {'cost': 0.001}, 'choices': [{'message': message}]})

team.httpx.AsyncClient = API
row = {'pricing': {'prompt': '0.000001', 'completion': '0.000001'}, 'supported_parameters': ['tools']}
args = SimpleNamespace(output=out, days=30, per_run_budget={'test/operator': 1}, timeout=60, max_turns=0,
    max_worker_steps=5, max_tokens=100, context_chars=60000, worker_context_chars=30000,
    worker='test/worker', resume=mode == 'resume', keep_open_on_error=True, source_hash='test')
if mode == 'budget':
    args.per_run_budget = {'test/operator': 0.0000001}
record = asyncio.run(team.play('test/operator', 1, args,
                               {'test/operator': row, 'test/worker': row}))
trial = out / 'test--operator-seed1-30d'
if mode in ('normal', 'resume'):
    assert record['completed'] and record['model_result_subtype'] == 'simulation_ended', record
    assert record['worker_turns'] >= 2 and record['setup'] == 'team', record
    worker_log = (trial / 'worker-conversation.jsonl').read_text()
    assert 'AI-LOUNGE-SNACK' in worker_log and 'What did you see?' in worker_log
    operator_log = (trial / 'conversation.jsonl').read_text()
    assert 'Checked AI-LOUNGE-SNACK' in operator_log
    assert 'Unknown business tool' in operator_log  # the operator cannot restock by hand
    assert record['resumed'] == (mode == 'resume'), record
    assert record['api_cost_usd'] > 0
elif mode == 'crash':
    assert record.get('resumable') and not (trial / 'result.json').exists(), record
    assert 'test-key-not-real' not in record['error']
elif mode == 'budget':
    assert record['model_result_subtype'] == 'budget_limit' and record['reward'] == 0, record
    assert not sent
print(json.dumps({k: record.get(k) for k in ('completed', 'reward', 'api_cost_usd', 'model_result_subtype')}))
'''


def run(path, mode):
    result = subprocess.run([sys.executable, '-c', SCRIPT, str(path), mode],
                            check=False, cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_team_completes_and_keeps_machine_work_with_the_worker(tmp_path):
    run(tmp_path, 'normal')


def test_budget_stops_before_any_request(tmp_path):
    run(tmp_path, 'budget')


def test_interrupted_trial_resumes_from_saved_world_and_conversations(tmp_path):
    run(tmp_path, 'crash')
    run(tmp_path, 'resume')
