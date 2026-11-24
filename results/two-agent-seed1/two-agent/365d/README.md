# Vending Bench — operator and worker teams

365-day challenge; seeds [1]; worker `deepseek/deepseek-v4.1-flash-1017`; budget per trial (USD, operator and worker together): openai/gpt-6.1-sol $30, anthropic/claude-opus-5.5 $120, google/gemini-4-argon $80, anthropic/claude-haiku-5.5 $10, deepseek/deepseek-v4.1-flash-1017 $15.

Each operator gets the business tools except the four machine tools, plus a worker it instructs with run_sub_agent. The worker has only the machine tools. Same world, briefing and seeds as the single-agent runner. Incomplete and bankrupt trials score zero. Exploratory, not a definitive ranking.

| Operator | Seed | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |
|---|---:|---|---:|---:|---:|---:|---:|---|
| [openai/gpt-6.1-sol](openai--gpt-6.1-sol-seed1-365d/result.json) | 1 | True | 365 | €55,046.59 | €54,451.18 | €54,448.46 | $2.96 | simulation_ended |
| [anthropic/claude-opus-5.5](anthropic--claude-opus-5.5-seed1-365d/result.json) | 1 | True | 365 | €52,005.53 | €51,148.73 | €51,139.89 | $9.61 | simulation_ended |
| [google/gemini-4-argon](google--gemini-4-argon-seed1-365d/result.json) | 1 | True | 365 | €43,132.60 | €41,980.86 | €41,960.89 | $21.70 | simulation_ended |
| [deepseek/deepseek-v4.1-flash-1017](deepseek--deepseek-v4.1-flash-1017-seed1-365d/result.json) | 1 | True | 365 | €35,693.34 | €35,489.16 | €35,488.03 | $1.23 | simulation_ended |
| [anthropic/claude-haiku-5.5](anthropic--claude-haiku-5.5-seed1-365d/result.json) | 1 | True | 365 | €5,849.81 | €4,952.79 | €4,951.59 | $1.31 | simulation_ended |

Finished trials: 5/5. Known API charges: $36.81.
