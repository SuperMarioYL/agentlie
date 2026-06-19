# Demo assets

The README's animated demo (`assets/demo.gif`) is rendered from
[`docs/demo.tape`](../docs/demo.tape) — a [VHS](https://github.com/charmbracelet/vhs)
script. CI renders and commits it automatically on every tag via
[`.github/workflows/demo.yml`](../.github/workflows/demo.yml).

To regenerate locally (needs the `agentlie` CLI on PATH):

```bash
pip install -e .
vhs docs/demo.tape       # writes assets/demo.gif
```

Also in this folder:

- `atlas-light.svg` / `atlas-dark.svg` — the architecture diagram used by the
  README's `## Architecture` / `## 架构` section (dark/light via `<picture>`).
