# Vending Bench — OpenRouter model comparison

365-day challenge; seeds [1]; $300.00 total request budget, $60.00 allocated per trial.

All agents receive the same briefing and 22 real MCP business tools. Each trial has an isolated simulator. Models do not receive simulator internals. Tool calls execute sequentially. History is a rolling window of complete exchanges; agents can use the business note tools for durable memory. Reasoning effort is low where supported; other generation settings use provider defaults.

This is an exploratory evaluation under the limits recorded in each harness.json, not a definitive model ranking. Incomplete and bankrupt trials score zero. API errors and limit stops remain visible. These 30-day balances are not comparable with full-year results or Claude CLI runs using a different harness.

| Model | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |
|---|---:|---|---:|---:|---:|---:|---:|---|

Finished trials: 0/5. Known API charges: $0.0000. Unresolved request reservations: $0.0000.

Score and profit are the business on its own; nothing charges the agent for thinking. The last column restates profit net of what the model actually cost to run, converted at a fixed €0.92/$ so the figure is reproducible. It is reported for interest and is not part of the ranking.

Catalog prices and model IDs are saved in `models.json`; exact source is in `evaluation-source.zip` and its SHA-256 in `evaluation-source.sha256`. Each trial retains its briefing, tool schemas, configuration, raw responses, conversation, generation IDs, billed usage and final simulator trajectory. Provider routing may vary; resolved model and provider data are retained.

API reference: [tool calling](https://openrouter.ai/docs/guides/features/tool-calling), [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
