# Vending Bench — operator and worker teams

30-day challenge; seeds [1]; worker `deepseek/deepseek-v4.1-flash-1017`; budget per trial (USD, operator and worker together): openai/gpt-6.1-sol $20, anthropic/claude-opus-5.5 $40, google/gemini-4-argon $20, anthropic/claude-haiku-5.5 $5, deepseek/deepseek-v4.1-flash-1017 $5.

Each operator gets the business tools except the four machine tools, plus a worker it instructs with run_sub_agent. The worker has only the machine tools. Same world, briefing and seeds as the single-agent runner. Incomplete and bankrupt trials score zero. Exploratory, not a definitive ranking.

| Operator | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |
|---|---:|---|---:|---:|---:|---:|---:|---|
| [google/gemini-4-argon](google--gemini-4-argon-seed1-30d/result.json) | 1 | True | 30 | €4,695.73 | €3,357.77 | €3,355.42 | $2.56 | simulation_ended |
| [anthropic/claude-opus-5.5](anthropic--claude-opus-5.5-seed1-30d/result.json) | 1 | True | 30 | €4,015.88 | €2,536.45 | €2,532.73 | $4.05 | simulation_ended |
| [openai/gpt-6.1-sol](openai--gpt-6.1-sol-seed1-30d/result.json) | 1 | True | 30 | €3,639.53 | €2,154.28 | €2,153.80 | $0.52 | simulation_ended |
| [deepseek/deepseek-v4.1-flash-1017](deepseek--deepseek-v4.1-flash-1017-seed1-30d/result.json) | 1 | True | 30 | €2,431.17 | €1,801.41 | €1,801.20 | $0.23 | simulation_ended |
| [anthropic/claude-haiku-5.5](anthropic--claude-haiku-5.5-seed1-30d/result.json) | 1 | True | 30 | €1,537.84 | €435.38 | €435.25 | $0.14 | simulation_ended |

Finished trials: 5/5. Known API charges: $7.49.
