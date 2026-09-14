#!/bin/bash
# Verifier for vending-bench.
#
# Reward is the completed run balance, otherwise zero. Tests validate the
# score contract; a business loss is a valid model outcome.

set -u

SIM_URL="${VENDING_SIM_URL:-http://sim-server:8000}"
OUT=/logs/verifier
mkdir -p "$OUT"

# Always (re)write the reward on every code path, overwriting anything the
# agent may have left behind.
echo 0 > "$OUT/reward.txt"

# Tooling first: the score fetch below needs curl, and a verifier image
# without it would otherwise score every run as zero.
apt-get update -qq && apt-get install -y -qq curl python3 >/dev/null 2>&1

# Seal first, then obtain artifacts. No live state can be read by an operating agent.
curl -fsS --max-time 120 -X POST "$SIM_URL/verifier/finalize" -o "$OUT/finalized.json" \
  || echo '{"score":{"error":"could not finalize simulation","reward":0}}' > "$OUT/finalized.json"
python3 - <<'PYCODE'
import json
from pathlib import Path
out = Path("/logs/verifier")
data = json.loads((out / "finalized.json").read_text())
(out / "vending_score.json").write_text(json.dumps(data["score"]))
(out / "vending_state.json").write_text(json.dumps(data.get("state", {})))
PYCODE

cat "$OUT/vending_score.json"

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh >/dev/null 2>&1
if [ -f "$HOME/.local/bin/env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.local/bin/env"
fi

uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf "$OUT/ctrf.json" /tests/test_outcome.py -rA
TEST_STATUS=$?

# Use the simulator's completion-gated reward.
python3 - <<'PY' > "$OUT/reward.txt"
import json
try:
    with open("/logs/verifier/vending_score.json") as fh:
        data = json.load(fh)
    value = float(data.get("reward", 0.0))
    if value != value or value in (float("inf"), float("-inf")):
        value = 0.0
except Exception:
    value = 0.0
print(f"{max(0.0, value):.2f}")
PY

echo "reward (EUR): $(cat "$OUT/reward.txt")"
exit $TEST_STATUS
