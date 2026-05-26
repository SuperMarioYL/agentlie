# Demo assets

The 30-second asciinema cast for the README is rendered from `demo.tape`
(a [VHS](https://github.com/charmbracelet/vhs) script). To regenerate:

```bash
vhs assets/demo.tape       # produces demo.gif
asciinema rec -c "bash examples/replay_demo.sh" assets/demo.cast --overwrite
```

The README references `demo.cast`; until you record it, the README falls
back to a still-image placeholder.
