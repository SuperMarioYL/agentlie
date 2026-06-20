"""Command-line entry point: ``agentlie check <session.jsonl>``."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from agentlie import __version__
from agentlie.codex import looks_like_codex, parse_codex_session
from agentlie.extractor import extract_claims, extract_claims_llm
from agentlie.models import Turn
from agentlie.models import Verdict
from agentlie.parser import FileStateTracker, parse_session
from agentlie.report import as_json, render_report
from agentlie.verifier import verify_session


def parse_any(session: Path, log_format: str) -> tuple[list[Turn], FileStateTracker]:
    """Dispatch to the Claude Code or Codex parser based on --format."""
    if log_format == "codex":
        return parse_codex_session(session)
    if log_format == "claude-code":
        return parse_session(session)
    # auto: sniff Codex first (it's the narrower signature), else Claude Code.
    if looks_like_codex(session):
        return parse_codex_session(session)
    return parse_session(session)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="agentlie")
def main() -> None:
    """agentlie — catch the moments your Claude Code Agent lied about a fix."""


@main.command()
@click.argument("session", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json_flag", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--offline/--llm-extract",
    default=True,
    help="Default offline: rule-based extractor only. --llm-extract enables the optional Claude-Haiku extractor (requires ANTHROPIC_API_KEY).",
)
@click.option("--fail-on-lie", is_flag=True, help="Exit 1 if any LIE verdict is emitted (CI use).")
@click.option("--no-evidence", is_flag=True, help="Hide evidence column.")
@click.option(
    "--format",
    "log_format",
    type=click.Choice(["auto", "claude-code", "codex"]),
    default="auto",
    help="Session log format. 'auto' sniffs Claude Code JSONL vs Codex logs.",
)
def check(
    session: Path,
    as_json_flag: bool,
    offline: bool,
    fail_on_lie: bool,
    no_evidence: bool,
    log_format: str,
) -> None:
    """Replay SESSION (a Claude Code .jsonl or Codex log) and verify every fix claim."""
    console = Console()
    turns, tracker = parse_any(session, log_format)
    if offline:
        pairs = extract_claims(turns)
    else:
        pairs, used_llm = extract_claims_llm(turns)
        if not used_llm:
            console.print(
                "[yellow]--llm-extract requested but no ANTHROPIC_API_KEY / anthropic SDK "
                "is available; falling back to rule-based extraction.[/yellow]"
            )
    verify_session(pairs, turns, tracker)

    if as_json_flag:
        click.echo(as_json(pairs))
    else:
        render_report(pairs, console=console, show_evidence=not no_evidence)

    if fail_on_lie and any(p.verdict == Verdict.LIE for p in pairs):
        sys.exit(1)


@main.command()
@click.argument("session", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def parse(session: Path) -> None:
    """Inspect parsed turns without running the verifier."""
    console = Console()
    turns, _ = parse_session(session)
    for turn in turns:
        tools = ", ".join(f"{e.tool}(path={e.path})" for e in turn.tool_calls) or "—"
        text_preview = (turn.assistant_text[:140] + "…") if len(turn.assistant_text) > 140 else turn.assistant_text
        console.print(f"[bold]Turn {turn.turn_id}[/bold]  tool_calls=[{tools}]")
        if text_preview:
            console.print(f"  [dim]assistant_text=[/dim] {text_preview}")
    console.print(f"\n[bold]{len(turns)} turns parsed[/bold]")


if __name__ == "__main__":
    main()
