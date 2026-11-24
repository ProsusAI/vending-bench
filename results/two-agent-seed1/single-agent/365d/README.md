# Vending Bench — OpenRouter model comparison

365-day challenge; seeds [1]; $750.00 total request budget, $150.00 allocated per trial.

All agents receive the same briefing and 22 real MCP business tools. Each trial has an isolated simulator. Models do not receive simulator internals. Tool calls execute sequentially. History is a rolling window of complete exchanges; agents can use the business note tools for durable memory. Reasoning effort is low where supported; other generation settings use provider defaults.

This is an exploratory evaluation under the limits recorded in each harness.json, not a definitive model ranking. Incomplete and bankrupt trials score zero. API errors and limit stops remain visible. These 30-day balances are not comparable with full-year results or Claude CLI runs using a different harness.

| Model | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |
|---|---:|---|---:|---:|---:|---:|---:|---|
| [google/gemini-4-argon](google--gemini-4-argon-seed1-365d/result.json) | 1 | True | 365 | €51,524.78 | €50,247.44 | €50,239.81 | $8.2936 | simulation_ended |
| [anthropic/claude-opus-5.5](anthropic--claude-opus-5.5-seed1-365d/result.json) | 1 | True | 365 | €46,098.08 | €45,079.06 | €45,008.43 | $76.7679 | simulation_ended |
| [deepseek/deepseek-v4.1-flash-1017](deepseek--deepseek-v4.1-flash-1017-seed1-365d/result.json) | 1 | True | 365 | €31,749.05 | €31,844.94 | €31,844.33 | $0.6590 | simulation_ended |
| [openai/gpt-6.1-sol](openai--gpt-6.1-sol-seed1-365d/result.json) | 1 | True | 365 | €13,612.44 | €12,326.25 | €12,324.41 | $1.9955 | simulation_ended |
| [anthropic/claude-haiku-5.5](anthropic--claude-haiku-5.5-seed1-365d/result.json) | 1 | True | 365 | €5,463.91 | €4,016.72 | €4,015.36 | $1.4790 | simulation_ended |

Finished trials: 5/5. Known API charges: $89.1950. Unresolved request reservations: $0.0000.

Score and profit are the business on its own; nothing charges the agent for thinking. The last column restates profit net of what the model actually cost to run, converted at a fixed €0.92/$ so the figure is reproducible. It is reported for interest and is not part of the ranking.

Catalog prices and model IDs are saved in `models.json`; exact source is in `evaluation-source.zip` and its SHA-256 in `evaluation-source.sha256`. Each trial retains its briefing, tool schemas, configuration, raw responses, conversation, generation IDs, billed usage and final simulator trajectory. Provider routing may vary; resolved model and provider data are retained.

API reference: [tool calling](https://openrouter.ai/docs/guides/features/tool-calling), [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
