#!/usr/bin/env bash
# sync_trades.sh — commit and push the latest paper_trades.csv and
# latency_snapshot.csv to GitHub so the Actions dashboard workflow picks them up.
#
# Run this every 15 minutes via the com.polymarket.sync LaunchAgent, or manually:
#   bash scripts/sync_trades.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PAPER_CSV="logs/paper_trades.csv"
LATENCY_CSV="logs/latency_snapshot.csv"

# Only stage files that actually exist
FILES_TO_ADD=()
[[ -f "$PAPER_CSV"  ]] && FILES_TO_ADD+=("$PAPER_CSV")
[[ -f "$LATENCY_CSV" ]] && FILES_TO_ADD+=("$LATENCY_CSV")

if [[ ${#FILES_TO_ADD[@]} -eq 0 ]]; then
    echo "[sync] No CSV files found — nothing to commit."
    exit 0
fi

git add --force "${FILES_TO_ADD[@]}"

if git diff --cached --quiet; then
    echo "[sync] No changes in CSVs since last push — skipping."
    exit 0
fi

# Build a commit message with a quick stats summary
STATS=$(python3 - <<'EOF'
import csv, pathlib, sys
p = pathlib.Path("logs/paper_trades.csv")
if not p.exists():
    print("no trades yet")
    sys.exit(0)
rows = [r for r in csv.DictReader(open(p)) if r.get("timestamp")]
if not rows:
    print("no trades yet")
    sys.exit(0)
last = rows[-1]
wr  = last.get("win_rate_pct", "—")
pnl = last.get("running_pnl", "—")
n   = last.get("total_trades", "0")
sign = "+" if float(pnl) >= 0 else ""
print(f"{n} trades | win={wr}% | P&L={sign}${float(pnl):.2f}")
EOF
)

TIMESTAMP="$(date -u +'%Y-%m-%d %H:%M UTC')"
git commit -m "data: sync paper trades [$TIMESTAMP] — $STATS"
git push origin HEAD

echo "[sync] Pushed: $STATS"
