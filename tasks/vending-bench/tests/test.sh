#!/bin/bash
# Verifier for vending-bench.
#
# Reward is the completed run balance, otherwise zero. Tests validate the
# score contract; a business loss is a valid model outcome.
#
# This runs in a separate container (task.toml: environment_mode = "separate").
# It never talks to a live simulator: a [[verifier.collect]] hook sealed the run
# inside sim-server after the agent's container stopped, and Harbor delivers
# that reply here as an artifact.
#
# A missing or unreadable score is a verifier fault, not a zero: those paths
# exit non-zero without writing a reward, so the trial is recorded as an error.

set -u

SEALED=/tmp/finalized.json
OUT=/logs/verifier
mkdir -p "$OUT"

if [ ! -s "$SEALED" ]; then
  echo "verifier error: no sealed score at $SEALED (collect hook or artifact failed)" >&2
  exit 1
fi
cp "$SEALED" "$OUT/finalized.json"

# Tooling: pytest is installed through uv below, which needs curl.
apt-get update -qq && apt-get install -y -qq curl python3 >/dev/null 2>&1

python3 - <<'PYCODE' || exit 1
import json
import math
import sys
from pathlib import Path

out = Path("/logs/verifier")
try:
    data = json.loads((out / "finalized.json").read_text())
    score = data["score"]
    reward = float(score["reward"])
except Exception as exc:  # malformed reply from sim-server
    sys.exit(f"verifier error: unreadable sealed score ({exc!r})")
if not math.isfinite(reward):
    sys.exit(f"verifier error: non-finite reward {reward!r}")
(out / "vending_score.json").write_text(json.dumps(score))
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

# Use the simulator's completion-gated reward. Written last, and only from a
# score that parsed above.
python3 - <<'PY' > "$OUT/reward.txt.tmp" || exit 1
import json
with open("/logs/verifier/vending_score.json") as fh:
    print(f"{max(0.0, float(json.load(fh)['reward'])):.2f}")
PY
mv "$OUT/reward.txt.tmp" "$OUT/reward.txt"

echo "reward (EUR): $(cat "$OUT/reward.txt")"
exit $TEST_STATUS
