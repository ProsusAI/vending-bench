# Vending Bench — OpenRouter model comparison

30-day challenge; seeds [1]; $100.00 total request budget, $20.00 allocated per trial.

All agents receive the same briefing and 22 real MCP business tools. Each trial has an isolated simulator. Models do not receive simulator internals. Tool calls execute sequentially. History is a rolling window of complete exchanges; agents can use the business note tools for durable memory. Reasoning effort is low where supported; other generation settings use provider defaults.

This is an exploratory evaluation under the limits recorded in each harness.json, not a definitive model ranking. Incomplete and bankrupt trials score zero. API errors and limit stops remain visible. These 30-day balances are not comparable with full-year results or Claude CLI runs using a different harness.

| Model | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |
|---|---:|---|---:|---:|---:|---:|---:|---|
| [google/gemini-4-argon](google--gemini-4-argon-seed1-30d/result.json) | 1 | True | 30 | €4,501.87 | €3,788.58 | €3,787.29 | $1.3981 | simulation_ended |
| [anthropic/claude-opus-5.5](anthropic--claude-opus-5.5-seed1-30d/result.json) | 1 | True | 30 | €3,881.90 | €2,711.01 | €2,705.46 | $6.0321 | simulation_ended |
| [deepseek/deepseek-v4.1-flash-1017](deepseek--deepseek-v4.1-flash-1017-seed1-30d/result.json) | 1 | True | 30 | €3,417.88 | €2,043.05 | €2,042.90 | $0.1589 | simulation_ended |
| [anthropic/claude-haiku-5.5](anthropic--claude-haiku-5.5-seed1-30d/result.json) | 1 | True | 30 | €1,750.94 | €466.61 | €466.45 | $0.1709 | simulation_ended |
| [openai/gpt-6.1-sol](openai--gpt-6.1-sol-seed1-30d/result.json) | 1 | False | 26 | €0.00 | €2,971.14 | €2,970.34 | unresolved | harness_or_api_error |

Finished trials: 5/5. Known API charges: $8.6246. Unresolved request reservations: $0.1992.

Score and profit are the business on its own; nothing charges the agent for thinking. The last column restates profit net of what the model actually cost to run, converted at a fixed €0.92/$ so the figure is reproducible. It is reported for interest and is not part of the ranking.

Catalog prices and model IDs are saved in `models.json`; exact source is in `evaluation-source.zip` and its SHA-256 in `evaluation-source.sha256`. Each trial retains its briefing, tool schemas, configuration, raw responses, conversation, generation IDs, billed usage and final simulator trajectory. Provider routing may vary; resolved model and provider data are retained.

API reference: [tool calling](https://openrouter.ai/docs/guides/features/tool-calling), [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
