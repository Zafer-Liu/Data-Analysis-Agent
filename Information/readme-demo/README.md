# README demo source

A 10-second, silent, looping illustration of the analysis workflow. Both language
versions of the project README embed the same GIF. The data and workspace are
illustrative, not a live recording of the application.

The timeline connects `sales.csv`, reveals the prompt and generated DuckDB SQL,
then builds a monthly revenue chart. The fictional values are 40, 48, 43, 58, 64,
and 72 thousand USD; January-to-June growth is `(72 / 40 - 1) × 100 = 80%`.
The final result holds for reading before the GIF loops.

## Edit and regenerate

Requires Node.js 22+ and FFmpeg on `PATH`. The CLI version is pinned in
`package.json`; no changes to the application's dependencies are needed.

```bash
cd Information/readme-demo
npm run dev
```

Edit `index.html` and review it in the HyperFrames preview. Then:

```bash
npm run check
npm run render
npm run still
```

Outputs:

- [`../assets/analysis-demo.gif`](../assets/analysis-demo.gif): 1000 × 600, 10 fps, infinite loop.
- [`../assets/analysis-demo.png`](../assets/analysis-demo.png): static preview at 8.5 seconds.

The chart adapts the [HyperFrames](https://github.com/heygen-com/hyperframes)
`animated-bar-chart` catalog component; the installed original is preserved in
`compositions/components/`. The smaller schema, SQL, and axis labels are secondary
UI detail; prioritize the question, chart, and result when adapting to narrower
layouts.

The source renders at 1200 × 720 and 12 fps. `export.mjs` then flattens the
background, resizes, and encodes a 96-color GIF to keep README loading fast.
