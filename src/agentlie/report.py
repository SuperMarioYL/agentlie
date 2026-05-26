"""Render a session verdict as a colored Rich table + optional JSON."""

from __future__ import annotations

import json
from collections import Counter
from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agentlie.models import ClaimEditPair, Verdict

VERDICT_STYLE = {
    Verdict.PASS: "bold green",
    Verdict.VAGUE: "bold yellow",
    Verdict.LIE: "bold red",
}

VERDICT_GLYPH = {
    Verdict.PASS: "✓",
    Verdict.VAGUE: "~",
    Verdict.LIE: "✗",
}


def summary(pairs: Iterable[ClaimEditPair]) -> Counter:
    return Counter(p.verdict for p in pairs)


def render_report(
    pairs: list[ClaimEditPair],
    *,
    console: Console | None = None,
    show_evidence: bool = True,
) -> None:
    console = console or Console()
    counts = summary(pairs)
    total = sum(counts.values())

    headline = Text()
    headline.append(f"{total} claims", style="bold")
    headline.append("  ·  ")
    headline.append(f"{counts[Verdict.PASS]} PASS", style=VERDICT_STYLE[Verdict.PASS])
    headline.append("  ·  ")
    headline.append(f"{counts[Verdict.VAGUE]} VAGUE", style=VERDICT_STYLE[Verdict.VAGUE])
    headline.append("  ·  ")
    headline.append(f"{counts[Verdict.LIE]} LIE", style=VERDICT_STYLE[Verdict.LIE])
    console.print(Panel(headline, title="agentlie verdict", border_style="blue"))

    if not pairs:
        console.print("[dim]No claims found in this session.[/dim]")
        return

    table = Table(show_lines=False, header_style="bold", expand=True)
    table.add_column("Turn", justify="right", style="dim", width=5)
    table.add_column("", width=2)
    table.add_column("Verb", width=8)
    table.add_column("Target", overflow="fold")
    table.add_column("Claim", overflow="fold")
    table.add_column("Edits", justify="right", width=5)
    if show_evidence:
        table.add_column("Evidence", overflow="fold")

    for pair in pairs:
        row = pair.to_row()
        glyph = Text(VERDICT_GLYPH[pair.verdict], style=VERDICT_STYLE[pair.verdict])
        cells = [
            str(row["turn"]),
            glyph,
            row["verb"],
            row["target"],
            row["claim"],
            str(row["edits"]),
        ]
        if show_evidence:
            cells.append(row["evidence"] or "")
        table.add_row(*cells, style=None if pair.verdict != Verdict.LIE else "red")
    console.print(table)


def as_json(pairs: list[ClaimEditPair]) -> str:
    """Stable JSON dump for CI / scripting."""
    payload = {
        "version": "0.1",
        "summary": {v.value: c for v, c in summary(pairs).items()},
        "total": len(pairs),
        "pairs": [
            {
                "turn_id": p.turn_id,
                "verdict": p.verdict.value,
                "claim": {
                    "text": p.claim.text,
                    "verb": p.claim.verb,
                    "target_path": p.claim.target_path,
                    "target_symbol": p.claim.target_symbol,
                },
                "edits": [
                    {
                        "tool": e.tool,
                        "path": e.path,
                        "source": e.source,
                        "ast_delta": e.ast_delta,
                    }
                    for e in p.edits
                ],
                "evidence": [{"code": r.code, "detail": r.detail} for r in p.evidence],
            }
            for p in pairs
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
