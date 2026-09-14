"""Combine OpenRouter batches, retaining failures and reconciling BYOK charges.

python scripts/report_openrouter.py results/batch-a results/batch-b --output results/summary
"""
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path


def read_trial(path):
    row = json.loads(path.read_text())
    harness = json.loads((path.parent / 'harness.json').read_text())
    timeout = harness['timeout_seconds']
    if (timeout is not None and row['runtime_seconds'] >= timeout and
            (row.get('error') or '').startswith(('ReadTimeout:', 'TimeoutError:', 'ConnectTimeout:'))):
        row['original_result_subtype'] = row['model_result_subtype']
        row['model_result_subtype'] = 'wall_clock_limit'
    responses = path.parent / 'responses.jsonl'
    router, upstream, missing = 0., 0., 0
    providers = set()
    if responses.exists():
        for line in responses.read_text().splitlines():
            data = json.loads(line)
            usage = data.get('usage') or {}
            if data.get('provider'):
                providers.add(data['provider'])
            if isinstance(usage.get('cost'), (int, float)):
                router += usage['cost']
            else:
                missing += 1
            if usage.get('is_byok'):
                cost = (usage.get('cost_details') or {}).get('upstream_inference_cost')
                if isinstance(cost, (int, float)):
                    upstream += cost
                else:
                    missing += 1
    tool_counts = Counter()
    receipts = path.parent / 'generation-metadata.json'
    resolved_models = sorted({r['model'] for r in json.loads(receipts.read_text())
                              if r.get('model')}) if receipts.exists() else []
    conversation = path.parent / 'conversation.jsonl'
    if conversation.exists():
        for line in conversation.read_text().splitlines():
            for message in json.loads(line):
                for call in message.get('tool_calls', []):
                    tool_counts[call['function']['name']] += 1
    return {**row, 'result_path': str(path.resolve()),
            'openrouter_charges_usd': router, 'byok_provider_charges_usd': upstream,
            'reconciled_known_cost_usd': router + upstream,
            'unpriced_responses': missing, 'providers': sorted(providers),
            'resolved_models_sampled': resolved_models,
            'tool_request_counts': dict(tool_counts)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('batches', type=Path, nargs='+')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    batches = [p.resolve() for p in args.batches if (p / 'manifest.json').exists()]
    rows = [read_trial(p) for batch in batches for p in batch.glob('*/result.json')]
    rows.sort(key=lambda r: (r['timestamp_utc'], r['model_requested']))
    groups = defaultdict(list)
    for row in rows:
        groups[row['model_requested']].append(row)
    router = sum(r['openrouter_charges_usd'] for r in rows)
    upstream = sum(r['byok_provider_charges_usd'] for r in rows)
    reserved = sum(r.get('unresolved_cost_reservation_usd', 0) for r in rows)
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {'trials': rows, 'model_count': len(groups),
               'openrouter_charges_usd': router, 'byok_provider_charges_usd': upstream,
               'known_total_cost_usd': router + upstream,
               'unresolved_request_reservations_usd': reserved}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    lines = ['# Vending Bench — OpenRouter comparison', '',
             f'{len(groups)} model families, {len(rows)} recorded trials. '
             'Every trial starts with €1,500, six machines, simulation seed 1 and a 30-day horizon.', '',
             f'**Known cost: ${router + upstream:.4f}**, consisting of '
             f'${router:.4f} in OpenRouter charges and ${upstream:.4f} in reported BYOK provider charges. '
             f'An additional ${reserved:.4f} is reserved for requests with unresolved billing. '
             'Unknown charges are not treated as zero.', '',
             '## Results', '',
             'The table shows the last scheduled attempt for each model. Recovery trials were '
             'scheduled only after infrastructure failures, using the remaining original allocation. '
             'Earlier failures remain in the full trial table below. This is not best-of-N selection. '
             'Costs in this table include all attempts for that model.', '',
             '| Model | Attempts | Completed | Day reached | Score | Final bank balance | Known cost, all attempts | Last outcome |',
             '|---|---:|---|---:|---:|---:|---:|---|']
    for model, attempts in sorted(groups.items(), key=lambda pair: (-pair[1][-1]['reward'], pair[0])):
        r = attempts[-1]
        link = os.path.relpath(r['result_path'], args.output)
        lines.append(f"| [{model}]({link}) | {len(attempts)} | {r['completed']} | "
                     f"{r['days_operated']} | €{r['reward']:,.2f} | €{r['final_balance']:,.2f} | "
                     f"${sum(x['reconciled_known_cost_usd'] for x in attempts):.4f} | "
                     f"{r['model_result_subtype']} |")
    lines += ['', '## How to interpret this', '',
              '- This is an exploratory test of model-plus-harness behavior, with one simulation seed. '
              'It does not establish a statistically reliable model ranking.',
              '- The initial allocation was $2.50 per model. Recovery attempts used $2.15–$2.49 '
              'after reserving costs from their failed attempts. Llama and Grok received $2.40 each '
              'from unused allocations after other models finished. The campaign target remained $20.',
              '- The runner stops before a conservative estimate of the next request exceeds the '
              'remaining allocation. A budget stop does not mean the model could not finish with more money.',
              '- Unfinished and bankrupt runs score zero. Reaching day 30 is not sufficient: '
              'the model must close the last simulated day. API failures are infrastructure outcomes, '
              'not evidence that a model cannot operate the business.',
              '- All trials use the same briefing, real MCP tool schemas and deterministic simulator. '
              'Agents see only business tools and their outputs. The harness uses a 60,000-character '
              'rolling history of complete exchanges and exposes note tools for durable memory. '
              'Reasoning effort is low where supported; temperature uses provider defaults. '
              'Each trial has a nominal 30-minute wall-clock limit and a 400-response limit. '
              'The original request timeout could overrun while a response was arriving: '
              'the final DeepSeek attempt lasted 30.6 minutes. Actual runtimes are retained below; '
              'the current runner now also applies an absolute asynchronous request deadline.',
              '- Provider fallbacks are allowed within catalog input/output price ceilings; '
              'model fallbacks are disabled. These ceilings can exclude more expensive routes. '
              'Latency, rate limits and transport failures therefore reflect the chosen routing '
              'constraints as well as the model and harness.',
              '- Batches may have been run against different harness revisions. The exact source '
              'for each batch is archived in its evaluation-source.zip; a harness revision can affect outcomes, '
              'so check the fingerprints before pooling batches.',
              '- Initial raw result files report OpenRouter fees alone. This consolidated report '
              'recomputes costs from preserved responses and adds upstream charges on BYOK requests '
              'without modifying the original run artifacts.',
              '- Results from this API harness must be reported separately from Harbor runs, '
              'Claude CLI runs, and from any other horizon.', '',
              '## Every trial, including failures', '',
              '| Model | Batch | Score | Completed | Minutes | Known cost | Unresolved reservation | Outcome |',
              '|---|---|---:|---|---:|---:|---:|---|']
    for r in rows:
        path = Path(r['result_path'])
        link = os.path.relpath(path, args.output)
        lines.append(f"| {r['model_requested']} | [{path.parent.parent.name}]({link}) | "
                     f"€{r['reward']:,.2f} | {r['completed']} | {r['runtime_seconds']/60:.1f} | "
                     f"${r['reconciled_known_cost_usd']:.4f} | "
                     f"${r.get('unresolved_cost_reservation_usd', 0):.4f} | "
                     f"{r['model_result_subtype']} |")
    lines += ['', '## Artifacts and API references', '',
              'Each batch contains a model catalog snapshot, manifest, source archive and source fingerprint. '
              'Each trial retains tool schemas, briefing, effective configuration, raw API responses, '
              'tool conversations, generation IDs, usage and final simulator trajectory. '
              'Where available, `generation-metadata.json` records first/last generation receipts '
              'with the resolved model identifiers from OpenRouter. '
              '`summary.json` adds reconciled costs and tool-use counts.', '',
              '[OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling), '
              '[usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting), '
              '[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection), '
              '[reasoning preservation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).', '']
    (args.output / 'README.md').write_text('\n'.join(lines))
    print(json.dumps({k: v for k, v in summary.items() if k != 'trials'}, indent=2))


if __name__ == '__main__':
    main()
