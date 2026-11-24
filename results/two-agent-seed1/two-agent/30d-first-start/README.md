# Vending Bench — operator and worker teams

30-day challenge; seeds [1]; worker `deepseek/deepseek-v4.1-flash-1017`; budget per trial (USD, operator and worker together): openai/gpt-6.1-sol $20, anthropic/claude-opus-5.5 $40, google/gemini-4-argon $20, anthropic/claude-haiku-5.5 $5, deepseek/deepseek-v4.1-flash-1017 $5.

Each operator gets the business tools except the four machine tools, plus a worker it instructs with run_sub_agent. The worker has only the machine tools. Same world, briefing and seeds as the single-agent runner. Incomplete and bankrupt trials score zero. Exploratory, not a definitive ranking.

| Operator | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |
|---|---:|---|---:|---:|---:|---:|---:|---|
| [openai/gpt-6.1-sol](openai--gpt-6.1-sol-seed1-30d/result.json) | 1 | True | 30 | €3,013.54 | €1,549.78 | €1,549.02 | $0.83 | simulation_ended |
| [anthropic/claude-opus-5.5](anthropic--claude-opus-5.5-seed1-30d/result.json) | 1 | True | 30 | €2,658.85 | €1,190.08 | €1,188.16 | $2.08 | simulation_ended |
| [anthropic/claude-haiku-5.5](anthropic--claude-haiku-5.5-seed1-30d/result.json) | 1 | True | 30 | €1,614.01 | €304.83 | €304.67 | $0.18 | simulation_ended |

Finished trials: 3/5. Known API charges: $3.09.
