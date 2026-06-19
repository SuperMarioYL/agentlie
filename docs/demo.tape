# vhs tape — agentlie happy path (cold clone → first verdict).
# Render in CI via .github/workflows/demo.yml, or locally:
#   vhs docs/demo.tape   (needs the `agentlie` CLI on PATH: pip install -e .)
Output assets/demo.gif

Set Shell "bash"
Set FontSize 17
Set Width 1180
Set Height 700
Set Theme "Dracula"
Set Padding 18

Type "# 1) Inspect a Claude Code session — the parser walks it per turn"
Enter
Sleep 600ms
Type "agentlie parse tests/fixtures/lying_transcript.jsonl"
Enter
Sleep 2.5s

Type "# 2) Verify every fix/add/remove/rename/update claim against real edits"
Enter
Sleep 600ms
Type "agentlie check tests/fixtures/lying_transcript.jsonl"
Enter
Sleep 3.5s

Type "# Red LIE rows = the agent claimed a change it never made."
Enter
Sleep 2.5s
