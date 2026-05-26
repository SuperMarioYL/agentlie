#!/usr/bin/env bash
# Replay the canonical lying transcript and print the verdict report.
#
# This is the 10-second demo: from a cold clone of this repo,
#   pip install -e .
#   bash examples/replay_demo.sh
# should print a colored table with at least one red LIE row.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
FIXTURE="${ROOT}/tests/fixtures/lying_transcript.jsonl"

if ! command -v agentlie >/dev/null 2>&1; then
  echo "agentlie is not on PATH. Install with: pip install -e ." >&2
  exit 1
fi

echo "── Replaying ${FIXTURE} ──"
echo
agentlie check "${FIXTURE}" || true
echo
echo "── JSON dump (first 1500 chars) ──"
agentlie check "${FIXTURE}" --json | head -c 1500
echo
