"""Run isolated, budget-limited OpenRouter agents through the real MCP tools.

uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv \
  python scripts/run_openrouter.py --env-file /path/to/.env --output results/openrouter
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
from itertools import count
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
import zipfile

import httpx
from dotenv import dotenv_values
from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / 'tasks/vending-bench/environment/sim-server'
# The simulated business keeps its books in euros and OpenRouter bills in
# dollars. Model cost is not charged to the business — the benchmark is whether
# the shop turns a profit — but every result also carries a profit-after-LLM-cost
# figure, and that needs one published rate rather than a moving market one.
EUR_PER_USD = 0.92
DEFAULT_MODELS = [
    'openai/gpt-5.6-luna', 'anthropic/claude-sonnet-5',
    'google/gemini-3.8-flash', 'deepseek/deepseek-v4-pro-0813',
    'qwen/qwen3.8-flash', 'moonshotai/kimi-k2.7-code',
    'z-ai/glm-5.3-flash', 'mistralai/mistral-small-2603',
]
MEMORY_POLICY = ('Conversation history uses a rolling window of complete tool exchanges. '
                 'Older exchanges may disappear. Use write_note and read_notes to retain '
                 'your own business plans and discoveries. Continue using the business tools '
                 'until the simulation ends. You may request multiple tools per response; '
                 'they execute sequentially in the order you give them.')


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temporary.replace(path)


def window(initial, exchanges, max_chars):
    """Keep whole assistant/tool groups, never orphan a tool response."""
    selected = []
    size = len(json.dumps(initial, ensure_ascii=False))
    for exchange in reversed(exchanges):
        length = len(json.dumps(exchange, ensure_ascii=False))
        if selected and size + length > max_chars:
            break
        selected.append(exchange)
        size += length
    return initial + [m for group in reversed(selected) for m in group]


def reserve_cost(payload, pricing):
    """Conservative uncached estimate: one input token per UTF-8 byte + framing.

    Provider price ceilings prohibit routes above the catalog rates. No requests
    are retried after ambiguous transport failures; their reservation is retained.
    """
    input_bound = len(json.dumps(payload, ensure_ascii=False).encode()) + 4096
    return (input_bound * float(pricing['prompt']) +
            payload['max_tokens'] * float(pricing['completion']) +
            float(pricing.get('request', 0))) * 1.10


def assistant_message(message):
    # Preserve provider reasoning signatures for subsequent tool turns.
    result = {k: v for k, v in message.items()
            if k in {'role', 'content', 'tool_calls', 'reasoning', 'reasoning_details'}
            and v is not None}
    if result.get('reasoning_details'):
        result.pop('reasoning', None)
    # Some OpenRouter fallback providers require content even on tool-only turns.
    result.setdefault('content', '')
    return result


def billed_cost(usage):
    """Include provider charges when OpenRouter routes through a BYOK integration."""
    router_cost = usage.get('cost')
    if not isinstance(router_cost, (int, float)) or router_cost < 0:
        raise ValueError('Missing valid OpenRouter cost')
    upstream = 0.
    if usage.get('is_byok'):
        upstream = (usage.get('cost_details') or {}).get('upstream_inference_cost')
        if not isinstance(upstream, (int, float)) or upstream < 0:
            raise ValueError('Missing valid BYOK provider cost')
    return router_cost + upstream


def error_outcome(exc, elapsed, timeout):
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) and timeout and elapsed >= timeout:
        return 'wall_clock_limit'
    return 'harness_or_api_error'


async def request_completion(api, payload, remaining_time, error_log):
    """Retry explicit rate-limit rejections only, never ambiguous billed failures."""
    deadline = time.monotonic() + remaining_time if remaining_time is not None else None
    for attempt in range(5):
        remaining = deadline - time.monotonic() if deadline is not None else float("inf")
        if remaining <= 0:
            raise TimeoutError('Wall-clock limit while waiting for rate limit')
        response = await asyncio.wait_for(
            api.post('chat/completions', json=payload, timeout=min(120, remaining)),
            timeout=remaining if deadline is not None else None,
        )
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {'error': response.text[:4000]}
            with error_log.open('a') as log:
                log.write(json.dumps({'status': response.status_code, 'body': body,
                                      'attempt': attempt + 1}) + '\n')
            # A response with a generation ID or usage may have incurred charges.
            if (response.status_code == 429 and attempt < 4 and
                    not body.get('id') and not body.get('usage')):
                await asyncio.sleep(min(2 ** (attempt + 1), remaining))
                continue
        response.raise_for_status()
        return response


async def execute_tools(client, calls, allowed):
    results = []
    for call in calls:
        try:
            name = call['function']['name']
            if name not in allowed:
                raise ValueError('Unknown business tool')
            arguments = json.loads(call['function']['arguments'])
            if not isinstance(arguments, dict):
                raise ValueError('Tool arguments must be a JSON object')
            result = await client.call_tool(name, arguments, raise_on_error=False)
            content = '\n'.join(c.text for c in result.content if getattr(c, 'text', None))
        except Exception as exc:
            content = f'Tool error: {type(exc).__name__}: {exc}'
        results.append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
    return results


async def play(model, seed, args, catalog):
    out = args.output / f'{model.replace("/", "--")}-seed{seed}-{args.days}d'
    out.mkdir(parents=True, exist_ok=False)
    # Each worker handles exactly one trial. Credentials never enter its artifacts.
    for key in list(os.environ):
        if key.startswith('VENDING_'):
            del os.environ[key]
    os.environ.update(VENDING_SEED=str(seed), VENDING_SIM_DAYS=str(args.days),
                      VENDING_STATE_PATH=str(out / 'live-state.json'), VENDING_RESUME='0')
    sys.path[:0] = [str(SIM), str(ROOT / 'scripts')]
    import server
    import sync_config
    from vending import config

    prompt = sync_config.render()
    (out / 'instruction.md').write_text(prompt + '\n\n' + MEMORY_POLICY)
    write_json(out / 'config.json', config.CONFIG)
    write_json(out / 'harness.json', {
        'harness': 'OpenRouter chat completions + in-process FastMCP client',
        'tools': 'Only the 22 MCP business tools; no source, shell or verifier access',
        'model_requested': model, 'budget_usd': args.per_run_budget,
        'timeout_seconds': args.timeout or None, 'max_turns': args.max_turns or None,
        'max_output_tokens': args.max_tokens, 'context_chars': args.context_chars,
        'memory_policy': MEMORY_POLICY, 'reasoning': 'low when supported; otherwise provider default',
        'routing': ('No model fallback; provider fallback allowed' +
                    (' within catalog price ceilings' if args.per_run_budget is not None else '; no price ceiling')),
        'tool_order': 'Sequential, including batches', 'temperature': 'provider default',
    })
    initial = [{'role': 'system', 'content': prompt + '\n\n' + MEMORY_POLICY},
               {'role': 'user', 'content': 'Operate the business until the simulation ends.'}]
    exchanges, usage_totals, generations = [], {}, []
    spent, uncertain, turns, empty_turns = 0., 0., 0, 0
    started = time.monotonic()
    outcome, error = 'turn_limit', None
    pricing = catalog['pricing']
    key = os.environ['OPENROUTER_API_KEY']
    try:
        async with Client(server.mcp) as client, httpx.AsyncClient(
            base_url='https://openrouter.ai/api/v1/', timeout=120,
            headers={'Authorization': f'Bearer {key}', 'X-Title': 'Vending Bench'},
        ) as api:
            tool_list = await client.list_tools()
            allowed = {tool.name for tool in tool_list}
            assert len(allowed) == 22
            tools = [{'type': 'function', 'function': {
                'name': tool.name, 'description': tool.description or '',
                'parameters': tool.inputSchema,
            }} for tool in tool_list]
            write_json(out / 'tools.json', tools)
            for turns in (range(1, args.max_turns + 1) if args.max_turns else count(1)):
                if server.ENGINE.over:
                    outcome = 'simulation_ended'
                    break
                remaining_time = args.timeout - (time.monotonic() - started) if args.timeout else None
                if remaining_time is not None and remaining_time <= 0:
                    outcome = 'wall_clock_limit'
                    break
                payload = {
                    'model': model, 'messages': window(initial, exchanges, args.context_chars),
                    'tools': tools, 'max_tokens': args.max_tokens,
                    'provider': {'require_parameters': True, 'max_price': {
                        'prompt': float(pricing['prompt']) * 1e6,
                        'completion': float(pricing['completion']) * 1e6,
                    }},
                }
                if args.per_run_budget is None:
                    payload['provider'].pop('max_price')
                if 'reasoning' in catalog.get('supported_parameters', []):
                    payload['reasoning'] = {'effort': 'low'}
                reserve = reserve_cost(payload, pricing)
                if args.per_run_budget is not None and spent + uncertain + reserve > args.per_run_budget:
                    outcome = 'budget_limit'
                    break
                uncertain += reserve
                # Never blindly retry a billed request with an unknown outcome.
                response = await request_completion(api, payload, remaining_time,
                                                    out / 'api-errors.jsonl')
                data = response.json()
                with (out / 'responses.jsonl').open('a') as log:
                    log.write(json.dumps(data, ensure_ascii=False) + '\n')
                usage = data.get('usage') or {}
                cost = billed_cost(usage)
                spent += cost
                uncertain -= reserve
                generations.append({'id': data.get('id'), 'model': data.get('model'),
                                    'provider': data.get('provider'), 'usage': usage})
                resolved = data.get('model', model)
                totals = usage_totals.setdefault(resolved, {'inputTokens': 0, 'outputTokens': 0, 'costUSD': 0.})
                totals['inputTokens'] += usage.get('prompt_tokens', 0)
                totals['outputTokens'] += usage.get('completion_tokens', 0)
                totals['costUSD'] += cost
                if data.get('error'):
                    raise RuntimeError('OpenRouter returned an error; see responses.jsonl')
                message = assistant_message(data['choices'][0]['message'])
                calls = message.get('tool_calls') or []
                exchange = [message]
                if calls:
                    empty_turns = 0
                    exchange += await execute_tools(client, calls, allowed)
                else:
                    empty_turns += 1
                    exchange.append({'role': 'user', 'content':
                        'Continue operating with the business tools until the simulation ends.'})
                exchanges.append(exchange)
                with (out / 'conversation.jsonl').open('a') as log:
                    log.write(json.dumps(exchange, ensure_ascii=False) + '\n')
                write_json(out / 'progress.json', {
                    'model': model, 'seed': seed, 'turns': turns,
                    'day': server.ENGINE.s['day'], 'tool_calls': server.ENGINE.s['tool_calls'],
                    'api_cost_usd': spent, 'runtime_seconds': time.monotonic() - started,
                })
                if server.ENGINE.over:
                    outcome = 'simulation_ended'
                    break
                if empty_turns >= 3:
                    outcome = 'agent_stopped_calling_tools'
                    break
    except Exception as exc:
        outcome = error_outcome(exc, time.monotonic() - started, args.timeout)
        error = f'{type(exc).__name__}: {exc}'.replace(key, '<redacted>')
    score = server.ENGINE.score(finalize=True)
    write_json(out / 'trajectory.json', server.ENGINE.s)
    write_json(out / 'generations.json', generations)
    llm_cost_eur = round(spent * EUR_PER_USD, 2)
    record = {
        **score, 'model_requested': model, 'model_usage': usage_totals,
        'horizon_days': args.days, 'seed': seed, 'api_cost_usd': spent if not uncertain else None,
        'known_api_cost_usd': spent, 'unresolved_cost_reservation_usd': max(0., uncertain),
        # The score above is the business alone. These restate it net of what
        # the model cost to run, which is reported but never scored.
        'llm_cost_eur': llm_cost_eur, 'eur_per_usd': EUR_PER_USD,
        'profit_after_llm_cost': round(score['profit'] - llm_cost_eur, 2),
        'cost_basis': 'OpenRouter usage.cost plus upstream_inference_cost for BYOK routes; '
                      'unknown requests retain their conservative reservation',
        'budget_usd': args.per_run_budget, 'runtime_seconds': round(time.monotonic() - started, 2),
        'model_result_subtype': outcome, 'error': error, 'api_turns': turns,
        'benchmark_sha256': args.source_hash,
        'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    write_json(out / 'result.json', record)
    return record


def worker(model, seed, args, catalog):
    return asyncio.run(play(model, seed, args, catalog))


def report(output):
    manifest = json.loads((output / 'manifest.json').read_text())
    rows = [json.loads(p.read_text()) for p in output.glob('*/result.json')]
    rows.sort(key=lambda r: (-r['reward'], r['model_requested'], r['seed']))
    budget_text = (f"${manifest['total_budget_usd']:.2f} total request budget, "
                   f"${manifest['per_run_budget_usd']:.2f} allocated per trial."
                   if manifest['total_budget_usd'] is not None else 'No dollar cap.')
    lines = ['# Vending Bench — OpenRouter model comparison', '',
             f"{manifest['days']}-day challenge; seeds {manifest['seeds']}; "
             f"{budget_text}", '',
             'All agents receive the same briefing and 22 real MCP business tools. '
             'Each trial has an isolated simulator. Models do not receive simulator internals. '
             'Tool calls execute sequentially. History is a rolling window of complete exchanges; '
             'agents can use the business note tools for durable memory. Reasoning effort is low '
             'where supported; other generation settings use provider defaults.', '',
             'This is an exploratory evaluation under the limits recorded in each harness.json, '
             'not a definitive model ranking. Incomplete and bankrupt trials score zero. '
             'API errors and limit stops remain visible. These 30-day balances are not comparable '
             'with full-year results or Claude CLI runs using a different harness.', '',
             '| Model | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |',
             '|---|---:|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        cost = f"${r['api_cost_usd']:.4f}" if r['api_cost_usd'] is not None else 'unresolved'
        slug = f"{r['model_requested'].replace('/', '--')}-seed{r['seed']}-{r['horizon_days']}d"
        lines.append(f"| [{r['model_requested']}]({slug}/result.json) | {r['seed']} | "
                     f"{r['completed']} | {r['days_operated']} | €{r['reward']:,.2f} | "
                     f"€{r['profit']:,.2f} | €{r['profit_after_llm_cost']:,.2f} | {cost} | "
                     f"{r['model_result_subtype']} |")
    lines += ['', f"Finished trials: {len(rows)}/{manifest['trials']}. "
              f"Known API charges: ${sum(r['known_api_cost_usd'] for r in rows):.4f}. "
              f"Unresolved request reservations: ${sum(r['unresolved_cost_reservation_usd'] for r in rows):.4f}.", '',
              'Score and profit are the business on its own; nothing charges the agent for '
              'thinking. The last column restates profit net of what the model actually cost '
              f"to run, converted at a fixed €{EUR_PER_USD:.2f}/$ so the figure is reproducible. "
              'It is reported for interest and is not part of the ranking.', '',
              'Catalog prices and model IDs are saved in `models.json`; exact source is in '
              '`evaluation-source.zip` and its SHA-256 in `evaluation-source.sha256`. '
              'Each trial retains its briefing, tool schemas, configuration, raw responses, '
              'conversation, generation IDs, billed usage and final simulator trajectory. '
              'Provider routing may vary; resolved model and provider data are retained.', '',
              'API reference: [tool calling](https://openrouter.ai/docs/guides/features/tool-calling), '
              '[usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).', '']
    (output / 'README.md').write_text('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--models', nargs='+', default=DEFAULT_MODELS)
    ap.add_argument('--seeds', nargs='+', type=int, default=[1])
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--total-budget', type=float, default=20, help='USD cap; 0 disables it')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--timeout', type=int, default=1800, help='Trial seconds; 0 disables the deadline')
    ap.add_argument('--max-turns', type=int, default=400, help='Model responses; 0 disables the limit')
    ap.add_argument('--max-tokens', type=int, default=4096)
    ap.add_argument('--context-chars', type=int, default=60000)
    ap.add_argument('--env-file', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.env_file:
        key = dotenv_values(args.env_file).get('OPENROUTER_API_KEY')
        if key:
            os.environ['OPENROUTER_API_KEY'] = key
    if not os.environ.get('OPENROUTER_API_KEY'):
        ap.error('OPENROUTER_API_KEY is required')
    if min(args.workers, args.days, args.max_tokens, args.context_chars) <= 0 or min(
            args.total_budget, args.timeout, args.max_turns) < 0:
        ap.error('Sizes must be positive; budget, timeout and max-turns may be zero (unlimited)')
    if len(set(args.models)) != len(args.models) or len(set(args.seeds)) != len(args.seeds):
        ap.error('Models and seeds must be unique')
    catalog_response = httpx.get('https://openrouter.ai/api/v1/models', timeout=30)
    catalog_response.raise_for_status()
    catalog = {m['id']: m for m in catalog_response.json()['data']}
    for model in args.models:
        if model not in catalog or 'tools' not in catalog[model].get('supported_parameters', []):
            ap.error(f'Model unavailable or lacks tool support: {model}')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    paths = sorted([*SIM.rglob('*.py'), *SIM.rglob('*.toml'),
                    *ROOT.glob('scripts/*.py'), *ROOT.glob('tests/*.py'),
                    *ROOT.glob('tasks/vending-bench/instruction*.md')])
    digest = hashlib.sha256()
    with zipfile.ZipFile(args.output / 'evaluation-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
            archive.write(path, path.relative_to(ROOT))
    args.source_hash = digest.hexdigest()
    (args.output / 'evaluation-source.sha256').write_text(args.source_hash + '\n')
    args.per_run_budget = args.total_budget / (len(args.models) * len(args.seeds)) if args.total_budget else None
    write_json(args.output / 'models.json', {m: catalog[m] for m in args.models})
    write_json(args.output / 'manifest.json', {
        'models': args.models, 'seeds': args.seeds, 'days': args.days,
        'total_budget_usd': args.total_budget or None, 'per_run_budget_usd': args.per_run_budget,
        'trials': len(args.models) * len(args.seeds), 'source_sha256': args.source_hash,
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })
    report(args.output)
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1,
                             mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = [pool.submit(worker, m, s, args, catalog[m])
                   for m in args.models for s in args.seeds]
        for future in as_completed(futures):
            row = future.result()
            print(json.dumps({k: row[k] for k in (
                'model_requested', 'seed', 'completed', 'reward', 'api_cost_usd',
                'runtime_seconds', 'model_result_subtype', 'error')}), flush=True)
            report(args.output)


if __name__ == '__main__':
    main()
