# VHS tape — record the 30-second agentlie demo.
# Run:  vhs assets/demo.tape  (produces demo.gif)

Output assets/demo.gif

Set FontSize 16
Set Width 1200
Set Height 720
Set Theme "Dracula"
Set Padding 16

Type "pip install agentlie"
Enter
Sleep 2s

Type "agentlie check tests/fixtures/lying_transcript.jsonl"
Enter
Sleep 4s

Type "# Look — 2 LIE rows in red. The agent claimed fixes it never made."
Enter
Sleep 3s
