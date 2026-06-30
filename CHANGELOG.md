# Changelog

All notable changes to this project will be documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - 2026-06-30

A fix-focused release. Five correctness defects in the honesty engine, the
replay tracker, and the CLI — found by a source-inspection bug hunt — are fixed,
so verdicts are trustworthy in more cases. No new feature scope.

### Fixed
- **Stop falsely flagging truthful non-structural edits as `LIE`.** An `add` or
  `remove` claim whose edit only touched a print, log call, assignment, return,
  or comment changed no structural AST node, so the verifier hard-set the AST
  evidence to "absent" and emitted `LIE` even though a real diff existed — the
  worst failure mode for an honesty tool, and it fired on all four supported
  languages. When a genuine textual diff is present, such a claim now resolves to
  `VAGUE` (or `PASS` on other evidence), never `LIE`. The zero-diff case is still
  a `LIE`.
- **Stop PASS-ing "I added X" when X already existed.** The symbol-presence check
  PASSed any claim whose named symbol was present after the edit, even when the
  symbol pre-existed and the agent changed an unrelated line (a missed lie). For
  `add` claims the symbol must now be newly introduced (absent before, present
  after) to count as evidence.
- **Make the basename fallback path-segment-safe.** Claim target `utils.py` could
  match `test/test_utils.py`, and `__init__.py` could match a different package's
  `__init__.py`, letting one file's edit back a claim about another file. The
  fallback now matches only at a real path boundary and is skipped entirely when
  the claim already names a directory-qualified path.
- **Model `Edit` with `replace_all: true` during replay.** The replay tracker
  hard-coded a first-occurrence-only replace, so a multi-occurrence edit was
  mis-reconstructed (`3 foo() → 1 bar() + 2 foo()`) and fed wrong ground truth to
  the verifier. `ActualEdit` now carries `replace_all`, and replay replaces every
  occurrence when the flag is set.
- **Make `agentlie parse` honor Codex logs.** The `parse` subcommand called the
  Claude-Code-only parser directly, so `agentlie parse <codex.log>` silently
  printed "0 turns parsed" on a format `check` handles fine. `parse` now routes
  through the shared format dispatcher and accepts the same `--format` option as
  `check`.

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
