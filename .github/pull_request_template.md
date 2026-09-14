**What does this change, and why?**

**Does it affect scores?**

- [ ] No — behaviour and economy are unchanged
- [ ] Yes — before/after scripted calibration is included below

**Checks**

- [ ] `uv run --with pytest --with fastmcp==2.11.3 python -m pytest tests/ -q`
- [ ] `python3 scripts/sync_config.py --check`
- [ ] Economic values live in `config.toml`, not in Python
- [ ] Generated files (`instruction.md`, `solution/world.json`) are regenerated
