#!/usr/bin/env bash
set -euo pipefail
python -m agentlie.cli check tests/fixtures/lying_transcript.jsonl --offline
python -m agentlie.cli check tests/fixtures/lying_transcript.jsonl --offline --json
