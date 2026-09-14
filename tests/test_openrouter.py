"""Budget accounting and real MCP execution without billed API calls."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_openrouter import assistant_message, billed_cost, error_outcome, execute_tools, request_completion, reserve_cost, window
import pytest
import httpx


def test_deadline_timeout_is_distinct_from_an_early_network_timeout():
    exc = httpx.ReadTimeout('timeout')
    assert error_outcome(exc, 1800.1, 1800) == 'wall_clock_limit'
    assert error_outcome(exc, 120, 1800) == 'harness_or_api_error'


def test_deadline_cancels_a_request_even_if_the_transport_keeps_waiting(tmp_path):
    class API:
        async def post(self, *args, **kwargs):
            await asyncio.sleep(10)
            raise AssertionError('Request should have been cancelled')
    with pytest.raises(TimeoutError):
        asyncio.run(request_completion(API(), {}, 0.02, tmp_path/'errors.jsonl'))


def test_byok_adds_provider_cost_without_double_counting_normal_routes():
    usage = {'cost': 0.01, 'cost_details': {'upstream_inference_cost': 0.2}}
    assert billed_cost(usage) == 0.01
    assert billed_cost({**usage, 'is_byok': True}) == pytest.approx(0.21)
    with pytest.raises(ValueError):
        billed_cost({'cost': 0, 'is_byok': True})


def test_signed_reasoning_is_preserved_without_duplicate_plaintext():
    details = [{'type': 'reasoning.encrypted', 'data': 'opaque-signature', 'id': 'call1'}]
    message = assistant_message({'role': 'assistant', 'reasoning': 'plain',
                                 'reasoning_details': details})
    assert message['reasoning_details'] == details
    assert 'reasoning' not in message
    assert message['content'] == ''


def test_explicit_rate_limit_retries_without_replaying_tools(tmp_path, monkeypatch):
    class Response:
        def __init__(self, status): self.status_code = status
        def json(self): return {'error': {'code': self.status_code}}
        def raise_for_status(self): assert self.status_code == 200
    class API:
        calls = 0
        async def post(self, *args, **kwargs):
            self.calls += 1
            return Response(429 if self.calls < 3 else 200)
    async def no_wait(seconds): pass
    monkeypatch.setattr(asyncio, 'sleep', no_wait)
    api = API()
    result = asyncio.run(request_completion(api, {}, 30, tmp_path/'errors.jsonl'))
    assert result.status_code == 200 and api.calls == 3
    assert len((tmp_path/'errors.jsonl').read_text().splitlines()) == 2


def test_window_keeps_tool_results_with_their_assistant():
    initial = [{'role': 'system', 'content': 'briefing'}]
    groups = [[{'role': 'assistant', 'content': 'x' * 200},
               {'role': 'tool', 'tool_call_id': str(i), 'content': 'result'}] for i in range(3)]
    result = window(initial, groups, 100)
    assert result == initial + groups[-1]


def test_cost_reserves_uncached_input_and_maximum_output():
    payload = {'messages': [{'content': 'hello €'}], 'max_tokens': 1000}
    estimate = reserve_cost(payload, {'prompt': '0.000002', 'completion': '0.00001'})
    assert estimate > 0.01 + len(json.dumps(payload).encode()) * 0.000002


def test_unregistered_tool_is_never_executed():
    class ForbiddenClient:
        async def call_tool(self, *args, **kwargs):
            raise AssertionError('Must not reach MCP')
    call = {'id': 'bad', 'function': {'name': 'finalize', 'arguments': '{}'}}
    result = asyncio.run(execute_tools(ForbiddenClient(), [call], {'get_status'}))
    assert result[0]['tool_call_id'] == 'bad'
    assert 'Unknown business tool' in result[0]['content']


def test_real_mcp_trial_and_missing_cost_stop(tmp_path):
    # Independent interpreter prevents simulator configuration leaking into other tests.
    code = '''
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import run_openrouter as runner
os.environ['OPENROUTER_API_KEY'] = 'test-key-not-real'
mode = sys.argv[2]
class Response:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        return {'id': 'test-generation', 'model': 'test/model',
                'usage': {'cost': 0.01} if mode in ('normal', 'unlimited') else {},
                'choices': [{'message': {'role': 'assistant', 'tool_calls': [
                    {'id': str(i), 'type': 'function', 'function': {
                        'name': 'wait_for_next_day', 'arguments': '{}'}} for i in range(30)]}}]}
class API:
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def post(self, path, **kwargs):
        assert mode != 'budget', 'Budget-exhausted trial must not send a request'
        if mode == 'unlimited': assert 'max_price' not in kwargs['json']['provider']
        return Response()
runner.httpx.AsyncClient = API
args = SimpleNamespace(output=Path(sys.argv[1]), days=30, per_run_budget=1,
    timeout=30, max_turns=3, max_tokens=100, context_chars=60000, source_hash='test')
if mode == 'budget': args.per_run_budget = 0.0000001
if mode == 'unlimited':
    args.per_run_budget = None
    args.timeout = args.max_turns = 0
record = asyncio.run(runner.play('test/model', 1, args,
    {'pricing': {'prompt': '0.000001', 'completion': '0.000001'}}))
if mode in ('normal', 'unlimited'):
    assert record['completed'], record
    assert record['tool_calls'] == 30, record
    assert record['api_cost_usd'] == 0.01, record
    assert record['model_result_subtype'] == 'simulation_ended', record
elif mode == 'budget':
    assert record['reward'] == 0 and record['tool_calls'] == 0, record
    assert record['api_cost_usd'] == 0, record
    assert record['model_result_subtype'] == 'budget_limit', record
else:
    assert not record['completed'], record
    assert record['reward'] == 0, record
    assert record['tool_calls'] == 0, record
    assert record['api_cost_usd'] is None, record
    assert record['unresolved_cost_reservation_usd'] > 0, record
    assert record['model_result_subtype'] == 'harness_or_api_error', record
'''
    for mode in ('normal', 'missing-cost', 'budget', 'unlimited'):
        path = tmp_path / mode
        path.mkdir()
        result = subprocess.run([sys.executable, '-c', code, str(path), mode],
                                cwd=ROOT, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
