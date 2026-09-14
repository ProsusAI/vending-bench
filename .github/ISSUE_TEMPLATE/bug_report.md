---
name: Bug report
about: Something in the simulator, runners or task behaves incorrectly
title: ''
labels: bug
assignees: ''
---

**What happened, and what did you expect instead?**

**How to reproduce**

```bash
# the exact command, including --seed and --days
```

**Environment**

- OS:
- Python version (`python3 --version`):
- How you ran it: in-process (`local_run.py`) / Claude CLI (`run_models.py`) / OpenRouter (`run_openrouter.py`) / Harbor + Docker
- Commit or release:

**Output**

<!-- Paste the relevant output. Remove any API keys first. -->

**Does this change scores?** If so, include a scripted calibration before and
after: `python3 scripts/run_baselines.py`
