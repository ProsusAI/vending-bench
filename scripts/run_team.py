"""Run an operator-and-worker team through the real MCP tools (OpenRouter).

The single-agent runner (run_openrouter.py) gives one model all 22 business
tools. This runner splits them the way the original Vending-Bench paper does:

  operator  plans, buys, emails, pays, markets and keeps notes. It never visits
            a machine itself; it hands physical work to the worker with
            run_sub_agent and can ask the worker questions with
            chat_with_sub_agent.
  worker    a second model with only the machine tools: it reads the machines
            and the depot, restocks, swaps and clears slots, and sets prices.

Everything else is the single-agent runner's: the same world, briefing, budget
reservation, price ceilings, provenance files and result record, so a team
result sits next to a single-agent result on the same seed. Notes
(write_note/read_notes) are the operator's scratchpad, kept by the simulator.

  uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv \\
    python scripts/run_team.py --models anthropic/claude-haiku-5.5 \\
    --worker deepseek/deepseek-v4.1-flash-1017 --days 30 --run-budget anthropic/claude-haiku-5.5=5 \\
    --output results/team/30d

Re-running the same command with --resume continues every trial that has no
result.json yet, from its saved world and conversations.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time

import httpx
from dotenv import dotenv_values
from fastmcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_openrouter as single  # shared request, cost and provenance helpers)

ROOT, SIM, EUR_PER_USD = single.ROOT, single.SIM, single.EUR_PER_USD
write_json = single.write_json

# The worker's tools: everything that happens at a machine, plus the two reads
# it needs to do that work. The operator keeps both reads too.
WORKER_TOOLS = {'get_machine_inventory', 'get_storage_inventory', 'restock_machine',
                'swap_item', 'clear_slot', 'set_price'}
OPERATOR_ONLY_HIDDEN = {'restock_machine', 'swap_item', 'clear_slot', 'set_price'}

TEAM_POLICY = (
    'You are the operator. You do not visit the machines yourself. A worker does all '
    'physical work: restocking, swapping and clearing slots, and setting prices. Give it '
    'complete, specific instructions with run_sub_agent (slot ids, product ids, quantities, '
    'prices); it carries them out and reports back. Ask it about what it did or saw with '
    'chat_with_sub_agent. You can still read machines and the depot yourself. '
    'Conversation history uses a rolling window of complete tool exchanges, so older '
    'exchanges may disappear: keep your plans and discoveries with write_note and '
    'read_notes. Continue using your tools until the simulation ends.')

WORKER_PROMPT = (
    'You are the worker for an autonomous vending business in Amsterdam: six machines at '
    'three locations (AI-LOUNGE, AI-HOUSE, MAIN-LOUNGE), each with a SNACK and a DRINKS '
    'machine, and one shared depot. The operator sends you instructions. Carry them out '
    'with your tools: read machines and the depot, restock slots from the depot, swap or '
    'clear slots, and set prices. Snack machines take food only (rows A and B small '
    'products, 18 units; rows C and D large products, 10 units); drinks machines take '
    'beverages, 10 units a slot. A slot with stock but no price sells nothing. Filling a '
    'machine takes 45 minutes of the working day and swapping a slot 30, so do what you '
    'were asked and no more. When you are done, reply without a tool call: a short report '
    'of what you did, what failed and why, and anything the operator should know.')

SUB_AGENT_TOOLS = [
    {'type': 'function', 'function': {
        'name': 'run_sub_agent',
        'description': ('Give the worker instructions and let it carry them out at the machines. '
                        'It can read the machines and the depot, restock, swap and clear slots, '
                        'and set prices. Returns the worker\'s report when it is done.'),
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['instructions'],
                       'properties': {'instructions': {
                           'type': 'string',
                           'description': 'What the worker should do, complete and specific.'}}}}},
    {'type': 'function', 'function': {
        'name': 'chat_with_sub_agent',
        'description': ('Ask the worker a question, for example what it did or saw on its last '
                        'task. It answers from memory and does not act.'),
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['message'],
                       'properties': {'message': {'type': 'string',
                                                  'description': 'Your question for the worker.'}}}}},
]
WORKER_CONTINUE = 'Carry on with the instructions, or reply without a tool call to report.'
OPERATOR_CONTINUE = 'Continue operating with your tools until the simulation ends.'


class Budget(Exception):
    """The run's next request would pass its budget."""


class Seat:
    """One model conversation: its windowed history, prices and spend."""

    def __init__(self, name, model, catalog_row, system, max_chars, log):
        self.name, self.model, self.row = name, model, catalog_row
        if model.startswith('anthropic/'):
            system = [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}]
        self.initial = [{'role': 'system', 'content': system}]
        self.exchanges, self.max_chars, self.log = [], max_chars, log

    def messages(self, extra=(), pin=None):
        """The rolling window; `pin` (an exchange index) is never dropped."""
        kept = single.window(self.initial, self.exchanges, self.max_chars)
        if pin is not None and len(self.exchanges) - pin > 0:
            tail = [m for group in self.exchanges[pin:] for m in group]
            if kept[-len(tail):] != tail:
                head = self.initial + self.exchanges[pin]
                budget = self.max_chars - len(json.dumps(head, ensure_ascii=False))
                kept = single.window(head, self.exchanges[pin + 1:], max(budget, 1))
        return kept + list(extra)

    def record(self, exchange):
        self.exchanges.append(exchange)
        with self.log.open('a') as fh:
            fh.write(json.dumps(exchange, ensure_ascii=False) + '\n')

    def reload(self):
        if self.log.exists():
            self.exchanges = [json.loads(line) for line in self.log.read_text().splitlines() if line]


class Team:
    def __init__(self, args, out, operator, worker, client, api):
        self.args, self.out, self.client, self.api = args, out, client, api
        self.operator, self.worker = operator, worker
        self.spent, self.uncertain, self.turns, self.worker_turns = 0., 0., 0, 0
        self.budget = args.per_run_budget[operator.model]
        self.usage, self.generations = {}, []
        self.started = time.monotonic()

    def remaining_time(self):
        return self.args.timeout - (time.monotonic() - self.started) if self.args.timeout else None

    async def complete(self, seat, messages, tools, talk_only=False):
        pricing = seat.row['pricing']
        payload = {'model': seat.model, 'messages': messages, 'max_tokens': self.args.max_tokens,
                   'provider': {'require_parameters': True, 'max_price': {
                       'prompt': float(pricing['prompt']) * 1e6,
                       'completion': float(pricing['completion']) * 1e6}}}
        if tools:
            payload['tools'] = tools
            if talk_only:
                payload['tool_choice'] = 'none'
        if 'reasoning' in seat.row.get('supported_parameters', []):
            payload['reasoning'] = {'effort': 'low'}
        reserve = single.reserve_cost(payload, pricing)
        if self.spent + self.uncertain + reserve > self.budget:
            raise Budget()
        remaining = self.remaining_time()
        if remaining is not None and remaining <= 0:
            raise TimeoutError('Wall-clock limit')
        self.uncertain += reserve
        response = await single.request_completion(self.api, payload, remaining,
                                                   self.out / 'api-errors.jsonl')
        data = response.json()
        with (self.out / 'responses.jsonl').open('a') as fh:
            fh.write(json.dumps({'seat': seat.name, **data}, ensure_ascii=False) + '\n')
        usage = data.get('usage') or {}
        cost = single.billed_cost(usage)
        self.spent += cost
        self.uncertain -= reserve
        self.generations.append({'seat': seat.name, 'id': data.get('id'), 'model': data.get('model'),
                                 'provider': data.get('provider'), 'usage': usage})
        totals = self.usage.setdefault(data.get('model', seat.model),
                                       {'inputTokens': 0, 'outputTokens': 0, 'costUSD': 0.})
        totals['inputTokens'] += usage.get('prompt_tokens', 0)
        totals['outputTokens'] += usage.get('completion_tokens', 0)
        totals['costUSD'] += cost
        if data.get('error'):
            raise RuntimeError('OpenRouter returned an error; see responses.jsonl')
        return single.assistant_message(data['choices'][0]['message'])

    async def run_worker(self, instructions):
        """One worker task: act until it reports without a tool call."""
        seat, engine = self.worker, self.args.engine
        seat.record([{'role': 'user', 'content': f'Instructions from the operator:\n{instructions}'}])
        pin = len(seat.exchanges) - 1
        for _ in range(self.args.max_worker_steps):
            if engine.over:
                return 'The simulation has ended.'
            message = await self.complete(seat, seat.messages(pin=pin), self.args.worker_tools)
            self.worker_turns += 1
            calls = message.get('tool_calls') or []
            if not calls:
                seat.record([message])
                return message.get('content') or '(The worker reported nothing.)'
            seat.record([message] + await single.execute_tools(self.client, calls, WORKER_TOOLS))
        ask = {'role': 'user', 'content': 'Step limit reached. Report now, without a tool call.'}
        message = await self.complete(seat, seat.messages([ask], pin=pin), self.args.worker_tools, True)
        message.pop('tool_calls', None)
        seat.record([ask, message])
        return ('(Stopped after the step limit.) ' + (message.get('content') or '')).strip()

    async def chat_worker(self, question):
        seat = self.worker
        ask = {'role': 'user', 'content': f'Question from the operator (answer, do not act):\n{question}'}
        message = await self.complete(seat, seat.messages([ask]), self.args.worker_tools, True)
        message.pop('tool_calls', None)
        seat.record([ask, message])
        return message.get('content') or '(No answer.)'

    async def operator_tools(self, calls):
        results = []
        for call in calls:
            name = call['function']['name']
            if name not in ('run_sub_agent', 'chat_with_sub_agent'):
                content = (await single.execute_tools(
                    self.client, [call], self.args.operator_allowed))[0]['content']
                results.append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
                continue
            field = 'instructions' if name == 'run_sub_agent' else 'message'
            try:
                text = json.loads(call['function']['arguments'] or '{}')[field]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f'{field} must be non-empty text')
            except Exception as exc:
                content = f'Tool error: {type(exc).__name__}: {exc}'
            else:
                content = await (self.run_worker(text) if name == 'run_sub_agent'
                                 else self.chat_worker(text))
            results.append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
        return results

    def progress(self, outcome=None):
        write_json(self.out / 'progress.json', {
            'model': self.operator.model, 'worker_model': self.worker.model, 'seed': self.args.seed,
            'turns': self.turns, 'worker_turns': self.worker_turns,
            'day': self.args.engine.s['day'], 'tool_calls': self.args.engine.s['tool_calls'],
            'api_cost_usd': self.spent, 'runtime_seconds': time.monotonic() - self.started,
            'outcome': outcome})


async def play(model, seed, args, catalog):
    out = args.output / f'{model.replace("/", "--")}-seed{seed}-{args.days}d'
    resuming = (out / 'live-state.json').exists() and not (out / 'result.json').exists()
    if (out / 'result.json').exists():
        return json.loads((out / 'result.json').read_text())
    if resuming and not args.resume:
        raise SystemExit(f'{out} has an unfinished trial; pass --resume to continue it')
    out.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if key.startswith('VENDING_'):
            del os.environ[key]
    os.environ.update(VENDING_SEED=str(seed), VENDING_SIM_DAYS=str(args.days),
                      VENDING_STATE_PATH=str(out / 'live-state.json'),
                      VENDING_RESUME='1' if resuming else '0')
    if args.days == 365:
        os.environ['VENDING_CONFIG'] = str(SIM / 'variants/full-year.toml')
    sys.path[:0] = [str(SIM), str(ROOT / 'scripts')]
    import server
    import sync_config
    from vending import config

    briefing = sync_config.render()
    prior = json.loads((out / 'progress.json').read_text()) if resuming else {}
    if not resuming:
        (out / 'instruction.md').write_text(briefing + '\n\n' + TEAM_POLICY)
        (out / 'worker-instruction.md').write_text(WORKER_PROMPT)
        write_json(out / 'config.json', config.CONFIG)
        write_json(out / 'harness.json', {
            'harness': 'Operator and worker team: OpenRouter chat completions + in-process FastMCP client',
            'model_requested': model, 'worker_model': args.worker,
            'operator_tools': 'The 22 MCP business tools minus restock_machine, swap_item, clear_slot '
                              'and set_price, plus run_sub_agent and chat_with_sub_agent',
            'worker_tools': sorted(WORKER_TOOLS), 'budget_usd': args.per_run_budget[model],
            'prompt_caching': 'Anthropic operators cache the tools and briefing (cost only)',
            'timeout_seconds': args.timeout or None, 'max_operator_turns': args.max_turns or None,
            'max_worker_steps': args.max_worker_steps, 'max_output_tokens': args.max_tokens,
            'context_chars': args.context_chars, 'worker_context_chars': args.worker_context_chars,
            'memory_policy': TEAM_POLICY, 'reasoning': 'low when supported; otherwise provider default',
            'routing': 'No model fallback; provider fallback allowed within catalog price ceilings',
            'tool_order': 'Sequential, including batches', 'temperature': 'provider default',
        })
    args.seed, args.engine = seed, server.ENGINE
    operator = Seat('operator', model, catalog[model], briefing + '\n\n' + TEAM_POLICY,
                    args.context_chars, out / 'conversation.jsonl')
    operator.initial.append({'role': 'user', 'content': 'Operate the business until the simulation ends.'})
    worker = Seat('worker', args.worker, catalog[args.worker], WORKER_PROMPT,
                  args.worker_context_chars, out / 'worker-conversation.jsonl')
    worker.initial.append({'role': 'user', 'content': 'Your work log with the operator follows.'})
    if resuming:
        operator.reload()
        worker.reload()
    key = os.environ['OPENROUTER_API_KEY']
    outcome, error, team, empty = 'turn_limit', None, None, 0
    try:
        async with Client(server.mcp) as client, httpx.AsyncClient(
                base_url='https://openrouter.ai/api/v1/', timeout=120,
                headers={'Authorization': f'Bearer {key}', 'X-Title': 'Vending Bench'}) as api:
            tool_list = await client.list_tools()
            assert len(tool_list) == 22
            schema = {t.name: {'type': 'function', 'function': {
                'name': t.name, 'description': t.description or '', 'parameters': t.inputSchema}}
                for t in tool_list}
            args.operator_allowed = set(schema) - OPERATOR_ONLY_HIDDEN
            operator_tools = [schema[n] for n in sorted(args.operator_allowed)] + SUB_AGENT_TOOLS
            args.worker_tools = [schema[n] for n in sorted(WORKER_TOOLS)]
            write_json(out / 'tools.json', {'operator': operator_tools, 'worker': args.worker_tools})
            team = Team(args, out, operator, worker, client, api)
            team.spent = prior.get('api_cost_usd', 0.)
            team.turns, team.worker_turns = prior.get('turns', 0), prior.get('worker_turns', 0)
            team.started -= prior.get('runtime_seconds', 0.)
            while True:
                team.turns += 1
                if server.ENGINE.over:
                    outcome = 'simulation_ended'
                    break
                if args.max_turns and team.turns > args.max_turns:
                    outcome = 'turn_limit'
                    break
                message = await team.complete(operator, operator.messages(), operator_tools)
                calls = message.get('tool_calls') or []
                exchange = [message]
                if calls:
                    empty = 0
                    exchange += await team.operator_tools(calls)
                else:
                    empty += 1
                    exchange.append({'role': 'user', 'content': OPERATOR_CONTINUE})
                operator.record(exchange)
                team.progress()
                if server.ENGINE.over:
                    outcome = 'simulation_ended'
                    break
                if empty >= 3:
                    outcome = 'agent_stopped_calling_tools'
                    break
    except Budget:
        outcome = 'budget_limit'
    except Exception as exc:
        outcome = single.error_outcome(exc, time.monotonic() - (team.started if team else 0), args.timeout)
        error = f'{type(exc).__name__}: {exc}'.replace(key, '<redacted>')
    if team:
        team.progress(outcome)
    if outcome == 'harness_or_api_error' and args.keep_open_on_error:
        # Leave the world unsealed so --resume can continue it.
        return {'model_requested': model, 'seed': seed, 'model_result_subtype': outcome, 'error': error,
                'completed': False, 'reward': 0., 'api_cost_usd': team.spent if team else 0.,
                'runtime_seconds': 0., 'resumable': True}
    score = server.ENGINE.score(finalize=True)
    write_json(out / 'trajectory.json', server.ENGINE.s)
    write_json(out / 'generations.json', team.generations if team else [])
    spent = team.spent if team else 0.
    uncertain = team.uncertain if team else 0.
    llm_cost_eur = round(spent * EUR_PER_USD, 2)
    record = {
        **score, 'setup': 'team', 'model_requested': model, 'worker_model': args.worker,
        'model_usage': team.usage if team else {}, 'horizon_days': args.days, 'seed': seed,
        'api_cost_usd': spent if not uncertain else None, 'known_api_cost_usd': spent,
        'unresolved_cost_reservation_usd': max(0., uncertain), 'llm_cost_eur': llm_cost_eur,
        'eur_per_usd': EUR_PER_USD, 'profit_after_llm_cost': round(score['profit'] - llm_cost_eur, 2),
        'cost_basis': 'OpenRouter usage.cost plus upstream_inference_cost for BYOK routes; '
                      'unknown requests retain their conservative reservation',
        'budget_usd': args.per_run_budget[model],
        'runtime_seconds': round(time.monotonic() - team.started, 2) if team else 0.,
        'model_result_subtype': outcome, 'error': error,
        'api_turns': team.turns if team else 0, 'worker_turns': team.worker_turns if team else 0,
        'resumed': resuming, 'benchmark_sha256': args.source_hash,
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
    lines = ['# Vending Bench — operator and worker teams', '',
             f"{manifest['days']}-day challenge; seeds {manifest['seeds']}; worker "
             f"`{manifest['worker']}`; budget per trial (USD, operator and worker together): "
             + ', '.join(f'{m} ${b:g}' for m, b in manifest['run_budget_usd'].items()) + '.', '',
             'Each operator gets the business tools except the four machine tools, plus a worker '
             'it instructs with run_sub_agent. The worker has only the machine tools. Same world, '
             'briefing and seeds as the single-agent runner. Incomplete and bankrupt trials score '
             'zero. Exploratory, not a definitive ranking.', '',
             '| Operator | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |',
             '|---|---:|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        cost = f"${r['api_cost_usd']:.2f}" if r['api_cost_usd'] is not None else 'unresolved'
        slug = f"{r['model_requested'].replace('/', '--')}-seed{r['seed']}-{r['horizon_days']}d"
        lines.append(f"| [{r['model_requested']}]({slug}/result.json) | {r['seed']} | {r['completed']} | "
                     f"{r['days_operated']} | €{r['reward']:,.2f} | €{r['profit']:,.2f} | "
                     f"€{r['profit_after_llm_cost']:,.2f} | {cost} | {r['model_result_subtype']} |")
    lines += ['', f"Finished trials: {len(rows)}/{manifest['trials']}. "
              f"Known API charges: ${sum(r['known_api_cost_usd'] for r in rows):.2f}.", '']
    (output / 'README.md').write_text('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--models', nargs='+', required=True, help='Operator models (OpenRouter ids)')
    ap.add_argument('--worker', default='deepseek/deepseek-v4.1-flash-1017')
    ap.add_argument('--seeds', nargs='+', type=int, default=[1])
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--run-budget', nargs='+', required=True, metavar='MODEL=USD',
                    help='Spend cap for each trial of that operator, worker included')
    ap.add_argument('--workers', type=int, default=5, help='Trials in parallel')
    ap.add_argument('--timeout', type=int, default=0, help='Trial seconds; 0 disables the deadline')
    ap.add_argument('--max-turns', type=int, default=0, help='Operator responses; 0 disables the limit')
    ap.add_argument('--max-worker-steps', type=int, default=30)
    ap.add_argument('--max-tokens', type=int, default=4096)
    ap.add_argument('--context-chars', type=int, default=60000)
    ap.add_argument('--worker-context-chars', type=int, default=30000)
    ap.add_argument('--resume', action='store_true', help='Continue unfinished trials in --output')
    ap.add_argument('--keep-open-on-error', action='store_true', default=True,
                    help=argparse.SUPPRESS)
    ap.add_argument('--env-file', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.env_file:
        key = dotenv_values(args.env_file).get('OPENROUTER_API_KEY')
        if key:
            os.environ['OPENROUTER_API_KEY'] = key
    if not os.environ.get('OPENROUTER_API_KEY'):
        ap.error('OPENROUTER_API_KEY is required')
    if len(set(args.models)) != len(args.models) or len(set(args.seeds)) != len(args.seeds):
        ap.error('Models and seeds must be unique')
    try:
        budgets = {m: float(v) for m, v in (item.rsplit('=', 1) for item in args.run_budget)}
    except ValueError:
        ap.error('--run-budget takes MODEL=USD pairs')
    if set(budgets) != set(args.models) or min(budgets.values()) <= 0:
        ap.error('--run-budget needs one positive amount for every operator model')
    response = httpx.get('https://openrouter.ai/api/v1/models', timeout=30)
    response.raise_for_status()
    catalog = {m['id']: m for m in response.json()['data']}
    for model in [*args.models, args.worker]:
        if model not in catalog or 'tools' not in catalog[model].get('supported_parameters', []):
            ap.error(f'Model unavailable or lacks tool support: {model}')
    args.output = args.output.resolve()
    manifest_path = args.output / 'manifest.json'
    if args.resume:
        if not manifest_path.exists():
            ap.error(f'Nothing to resume in {args.output}')
        manifest = json.loads(manifest_path.read_text())
        args.source_hash = manifest['source_sha256']
        args.per_run_budget = manifest['run_budget_usd']
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        args.source_hash = _source_archive(args.output)
        args.per_run_budget = budgets
        write_json(args.output / 'models.json', {m: catalog[m] for m in [*args.models, args.worker]})
        write_json(manifest_path, {
            'setup': 'team', 'models': args.models, 'worker': args.worker, 'seeds': args.seeds,
            'days': args.days, 'run_budget_usd': budgets,
            'total_budget_usd': sum(budgets.values()) * len(args.seeds),
            'trials': len(args.models) * len(args.seeds),
            'source_sha256': args.source_hash,
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    report(args.output)
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1,
                             mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = [pool.submit(worker, m, s, args, catalog) for m in args.models for s in args.seeds]
        for future in as_completed(futures):
            row = future.result()
            print(json.dumps({k: row.get(k) for k in (
                'model_requested', 'seed', 'completed', 'reward', 'api_cost_usd',
                'model_result_subtype', 'error', 'resumable')}), flush=True)
            report(args.output)


def _source_archive(output):
    """The same source fingerprint as the single-agent runner."""
    import hashlib
    import zipfile
    paths = sorted([*SIM.rglob('*.py'), *SIM.rglob('*.toml'), *ROOT.glob('scripts/*.py'),
                    *ROOT.glob('tests/*.py'), *ROOT.glob('tasks/vending-bench/instruction*.md')])
    digest = hashlib.sha256()
    with zipfile.ZipFile(output / 'evaluation-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
            archive.write(path, path.relative_to(ROOT))
    (output / 'evaluation-source.sha256').write_text(digest.hexdigest() + '\n')
    return digest.hexdigest()


if __name__ == '__main__':
    main()
