# Contributing to Prosus Vending Bench

Thanks for your interest. This is an open benchmark, and we welcome bug reports,
economy fixes, new variants and result contributions.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating, you agree to uphold it.

## Reporting bugs

Open an issue using the **Bug report** template. Please include:

- What you expected versus what happened
- The exact command, the seed, and the horizon (`--days`)
- Your OS and Python version (`python3 --version`), and whether you ran the
  in-process runner, the OpenRouter runner or Harbor with Docker
- Relevant output, with any API keys removed

Do **not** file security vulnerabilities as public issues — see
[SECURITY.md](SECURITY.md).

## Suggesting changes to the economy

The simulated economy is deliberately open. If you think a parameter is wrong,
say what behaviour it produces and what it should produce, and include a
scripted calibration run at both horizons:

```bash
python3 scripts/run_baselines.py
```

A change that makes the benchmark trivially winnable or unwinnable for the
scripted reference will be rejected, so include the numbers.

## Development setup

Python 3.11+. `uv` keeps the test dependencies out of your global environment.

```bash
git clone https://github.com/ProsusAI/vending-bench.git
cd vending-bench

# Fast smoke test: scripted operator, no model, no Docker
python3 scripts/local_run.py --seed 1

# Full suite, including real MCP transport checks
uv run --with pytest --with fastmcp==2.11.3 python -m pytest tests/ -q
```

## Project conventions

- **The world lives in `config.toml`.** Every economic number, persona, product
  and supplier belongs in
  `tasks/vending-bench/environment/sim-server/vending/config.toml`. Do not put
  economic values in Python. A variant should be a short `extends` overlay, not
  a copy of the file.
- **Generated files are generated.** `instruction.md` and `solution/world.json`
  are rendered from the config. Edit the template and regenerate:
  ```bash
  python3 scripts/sync_config.py
  python3 scripts/sync_config.py --check   # CI-style staleness check
  ```
- **Do not change a horizon in one place only.** A briefing that disagrees with
  the simulator lies to the agent. Change the config and regenerate, or use
  `scripts/prepare_task.py` to materialize a self-contained variant task.
- **Keep the scoring contract intact.** Reward is the nonnegative final bank
  balance after a completed run. There is no live state or score endpoint, and
  finalizing seals a run before returning anything. Changes to that surface need
  a strong reason and a test.
- **Never mix harnesses or horizons in one ranking.** A 30-day score is not a
  365-day score, and a Harbor run with broad tool permissions is not an
  MCP-only run.

## Pull requests

1. Fork the repo and branch from `main` (`fix/...` or `feat/...`).
2. Keep changes focused — one logical change per PR.
3. Make sure the suite passes and the generated files are in sync:
   ```bash
   uv run --with pytest --with fastmcp==2.11.3 python -m pytest tests/ -q
   python3 scripts/sync_config.py --check
   ```
4. Add tests for new behaviour where practical.
5. Say in the PR whether the change affects scores. If it does, include before
   and after baselines.

## Contributing results

Model results are welcome, but they must be reproducible and honestly labelled.
Include the harness, the allowed tool surface, the horizon, the seeds, the
source fingerprint and every failed attempt. Unfinished runs score zero and stay
in the table. Do not submit best-of-N selections, and do not submit results that
pool different harnesses or horizons.
