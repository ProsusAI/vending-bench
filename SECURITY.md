# Security Policy

## Supported versions

Prosus Vending Bench is released from the `main` branch. Fixes are applied to
the latest release only.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Use one of these private channels instead:

1. **GitHub private vulnerability reporting** (preferred): the repository's
   **Security** tab → **Report a vulnerability**.
2. **Email:** `cyber@prosus.com`

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce or a proof of concept
- Affected version or commit
- Any suggested mitigation

We will acknowledge receipt within **5 business days** and aim to give an
assessment and a remediation timeline within **15 business days**. Please give
us a reasonable opportunity to address the issue before public disclosure.

## Scope and design notes

This repository is a benchmark, not a hosted service. A few design points worth
knowing when assessing a report:

- **The simulator is not multi-tenant.** `sim-server` holds one world in one
  process with no authentication, and it is meant to run on a private network
  inside a single trial's Docker Compose project. Exposing it to a shared
  network is a deployment mistake, not a supported configuration. Isolate every
  trial.
- **The agent under test is untrusted by design.** The scoring contract assumes
  it will probe. There is no live `/state` or `/score` endpoint;
  `POST /verifier/finalize` irreversibly seals a run *before* returning the
  score, so an agent that reaches it forfeits the run rather than gaining an
  advantage. Sealing is serialized with tool calls. A way to read or change the
  score without sealing, or to keep operating after sealing, is a real finding.
- **Configuration is arbitrary-code-adjacent.** `VENDING_CONFIG`, `VENDING_SET` and
  the `VENDING_*` variables load and override a TOML file from disk. Only point
  them at files you control.
- **No credentials live here.** `scripts/run_openrouter.py` reads only
  `OPENROUTER_API_KEY` from an env file you pass explicitly, and
  `scripts/run_models.py` uses your already-authenticated Claude CLI. Keys are
  never written into results. A path that leaks a key into a result directory,
  a log or a trajectory is a real finding.
- **Published results contain model output.** Trajectories and conversations
  under `results/` are raw model text. Treat them as untrusted data, not as
  instructions.

If you find a way to read or alter a score without sealing the run, escape the
simulated tool surface, or leak credentials into results, we want to hear about
it.
