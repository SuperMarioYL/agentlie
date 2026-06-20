# Changelog

All notable changes to this project will be documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-06-20

### Fixed
- Replace the uninstallable `tree-sitter-languages` dependency with
  `tree-sitter-language-pack`, so the AST verifier actually runs on
  Python 3.12 instead of silently skipping every AST code path. The
  headline "string + AST checks" promise is now real, not degraded to
  string-only.
- Make the parentUuid DAG walk iterative so long coding-agent sessions
  (~1500+ records, a near-linear chain) no longer crash with
  `RecursionError`.
- Stop the `fix`-verb branch from flipping a genuine fix to `LIE` based on
  edit iteration order — a real fix plus a noop edit to the same target
  now PASSes regardless of order.
- Always attach evidence to `rename` / `update` verdicts (previously the
  PASS path emitted none).

### Added
- `--llm-extract` is now a real Claude-Haiku-backed extractor (off by
  default for offline use) that augments the rule-based pass and degrades
  gracefully to regex when no `ANTHROPIC_API_KEY` is present.
- AST verification coverage for Go and Rust — `.go` / `.rs` claims can now
  reach `PASS` / `LIE` verdicts instead of degrading to `VAGUE`.
- Codex log-format parsing (`--format codex`, auto-sniffed by default)
  via `src/agentlie/codex.py`, normalising Codex session logs into the
  same `Turn` / `ActualEdit` model so `agentlie check` works on Codex
  transcripts.

## [0.1.0] - 2026-05-26

### Added
- `agentlie check <session.jsonl>` — replay a Claude Code session,
  extract every fix/add/remove/rename/update claim, verify each against
  the actual file mutations, render a colored verdict table.
- `agentlie parse <session.jsonl>` — inspect parsed turns without
  running the verifier.
- `--json` output for CI use.
- `--fail-on-lie` exit-code gate.
- Per-turn `FileStateTracker` that prefers `toolUseResult.originalFile`
  + `structuredPatch` ground-truth and falls back to cumulative replay.
- Rule-based claim extractor (Python + TypeScript verbs).
- Tree-sitter AST delta for Python and TypeScript verdicts.
- Fixture transcript with planted lies so the demo reproduces cold.
