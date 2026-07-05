# Changelog

All notable changes to this project will be documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.5.0] - 2026-07-06

A correctness-focused release. The strongest verified honesty-engine defect this
cycle — a lying "I removed X" that scored PASS — is fixed, a low-severity
`--json` provenance-label bug is closed, and AST verdicts now cover Ruby, so
verdicts are trustworthy on more transcripts and more languages.

### Fixed
- **A `remove` claim whose named symbol is still present after the edit no longer
  false-PASSes.** The symbol-string-evidence check credited `symbol_present_after`
  for *any* verb, but for a removal claim the symbol being present *after* the edit
  is precisely counter-evidence — the removal never happened. A lying "I removed X"
  where X remained scored **PASS** (the honesty engine's worst failure mode: a
  missed lie). Now a removal claim treats a still-present symbol as
  `symbol_still_present` counter-evidence and never credits it; only the genuine
  present-before / absent-after transition (`symbol_removed`) can PASS a remove +
  symbol claim, so a lie resolves to LIE (AST/diff contradicts) or VAGUE (no ground
  truth). This is the inverse-sibling of the v0.3.0 `add`-symbol pre-existing fix.
  (`src/agentlie/verifier.py`)
- **The `--json` `source` field no longer mislabels replay-derived edits as
  `originalFile`.** `edit.source` was stamped from a sticky per-path set, so the
  second and later edits to a path whose `originalFile` was seeded once were labeled
  `originalFile` even though their before-state came from cumulative replay. Now an
  edit is `originalFile`-sourced only when its before-state IS the seeded original
  (the first edit to consume it); subsequent edits are `replay`. Verdict-neutral, but
  the machine-readable ground-truth-origin field is now honest for CI consumers.
  (`src/agentlie/parser.py`)

### Added
- **AST verdicts now cover Ruby** (Python, TypeScript, Go, Rust, Java, **Ruby**).
  Ruby's grammar is already in the installed `tree-sitter-language-pack`, so this
  adds no new dependency. `.rb` maps to the `ruby` parser, the Ruby structural node
  types (`method`, `singleton_method`, `class`, `module`) join `ADD_INDICATORS`, and
  `PATH_PATTERN` recognizes `.rb`, so Ruby add/remove claims produce PASS/LIE through
  the same AST-delta path as the other five languages instead of degrading to VAGUE.
  (`src/agentlie/verifier.py`, `src/agentlie/extractor.py`)

## [0.4.0] - 2026-07-03

A correctness-focused release. The strongest verified honesty-engine defect on
the Codex format is fixed, a sibling target-resolution bug is closed, and AST
verdicts now cover Java — so verdicts are trustworthy on more transcripts and
more languages.

### Fixed
- **Codex `apply_patch` no longer discards the before-state.** Every hunk (Add,
  Update, Delete) used to be modelled as a bare `Write` of only the after-fragment
  (context + `+` lines), leaving `before_content` `None`. That made a genuine
  Codex **removal** claim un-confirmable (it fell to `VAGUE` because
  `symbol_removed` needs the symbol present-before/absent-after), and it defeated
  the v0.3.0 pre-existing-symbol guard (which needs to see the symbol in the
  before). An Update hunk now reconstructs **both** sides — before = context + `-`
  lines, after = context + `+` lines — so removal claims can `PASS` and an "added
  X" claim about a symbol the patch's context shows already existed is caught.
- **Extractor target-path resolution is now path-segment-safe.** A basename was
  matched with a bare substring test, so `config.py` matched inside the unrelated
  word `reconfig.python` and `utils.py` matched inside `test_utils.py` — the same
  defect class the v0.3.0 verifier fix repaired, one layer earlier. Matches now
  require a real path-token boundary.

### Added
- **Java AST verification.** `.java` claims previously always degraded to `VAGUE`;
  they now reach the same AST-delta verdict path (`PASS`/`LIE`) as Python,
  TypeScript, Go, and Rust. The grammar ships in the existing
  `tree-sitter-language-pack` dependency, so there is no new dependency.

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
