# Video and audit report sources

Everything needed to rebuild the silent walkthrough (`docs/demo/demo.mp4`) and the audit report
(`docs/demo/AUDIT_REPORT.*`) from this repository alone. Paths below are relative to the repo root.

## What is here

| Path | What it is |
|---|---|
| `index.html` | Root of the video: theme (colours, type), and the timing of every slide |
| `compositions/bg.html`, `compositions/chrome.html` | Background grid, and the persistent project label, slide counter and progress bar |
| `compositions/s01-context.html` | Slide 01, the industry and four everyday problems |
| `compositions/s02-hook.html` | Slide 02, the project in one sentence |
| `compositions/explainer.html` | Slides 03-04, the white "how it works" chapter (edit its `CONFIG` block; slide B's `IV` array holds three real intervals from the search log) |
| `compositions/s03-architecture.html` | Slide 05, the layered architecture diagram (`BANDS`, `NODES`, `WIRES`, `LABELS`) |
| `compositions/s04-running.html` | Slide 06, the agent's search round by round and the hold-out result (data injected as `WALK`) |
| `compositions/value.html` | Slide 07, the planner's change scorecard and the DC-to-store scene (data injected as `SHEET`) |
| `compositions/s05-results.html`, `s06-twist.html`, `s07-end.html` | Slides 08-10, results, what didn't win, end card |
| `assets/search_walk.json` | Every candidate the search tested, exported from `outputs/search_neighbour_promo.*` by `tools/export_search_walk.py` |
| `assets/change_sheet.json` | The seven named strategies on the promotion quarter (30 seeds) plus the store list, exported by `tools/export_change_sheet.py` |
| `audit/facts.yaml` | Every figure (F-xx), assumption (A-xx), slide text and check expression used by the report |
| `tools/build_audit_report.py` | Builds the slide stills, re-checks every figure against the repo outputs, writes MD, DOCX and PDF |
| `tools/export_search_walk.py` | Reads the search log in `outputs/` and writes `assets/search_walk.json` (no re-run) |
| `tools/export_change_sheet.py` | Re-runs the repo's own evaluation of the promotion quarter (same data, sigma and seeds as `make run`) and writes `assets/change_sheet.json` |
| `tools/inject_sheet.py` | Inlines a JSON file into a composition between `/* NAME:BEGIN */ … /* NAME:END */` markers (renders cannot fetch files) |
| `package.json`, `hyperframes.json`, `meta.json` | HyperFrames project files (CLI pinned to 0.8.156) |

The repo writes no plots, so slide 06 is drawn directly from the exported search log rather than
from a converted matplotlib image. Fonts (Inter, JetBrains Mono, Montserrat) are referenced by name
and embedded by the HyperFrames renderer; all three are SIL Open Font License.

## Requirements

- Node 22 and `npx` (HyperFrames 0.8.156 is fetched by `npx`), plus Chrome/Chromium for rendering.
- `ffmpeg` for compressing the video and extracting slide stills.
- LibreOffice (`soffice`) to convert the report to PDF.
- The repo's own environment (`uv sync`) for `make run` and `export_change_sheet.py`.
- For `build_audit_report.py`, `export_search_walk.py` and `inject_sheet.py` (any Python 3.11+, not added
  to the repo's `pyproject.toml`): `pip install pyyaml pandas python-docx pillow numpy matplotlib`.

## Rebuild the numbers, the video and the report

```bash
# 1. repo outputs (about 2.5 minutes on 4 cores)
make run

# 2. inputs for the video
python3 docs/demo/source/tools/export_search_walk.py docs/demo/source/assets/search_walk.json
PYTHONPATH=src .venv/bin/python docs/demo/source/tools/export_change_sheet.py docs/demo/source/assets/change_sheet.json
python3 docs/demo/source/tools/inject_sheet.py docs/demo/source/assets/search_walk.json \
  docs/demo/source/compositions/s04-running.html --name WALK
python3 docs/demo/source/tools/inject_sheet.py docs/demo/source/assets/change_sheet.json \
  docs/demo/source/compositions/value.html --name SHEET

# 3. render (90.6 s, 1920x1080, 30 fps, silent) and compress
cd docs/demo/source
npx --yes hyperframes@0.8.156 check
npx --yes hyperframes@0.8.156 render -f 30 -q looks -o renders/supply-chain-twin-master.mp4
cd -
ffmpeg -y -i docs/demo/source/renders/supply-chain-twin-master.mp4 -c:v libx264 -preset slow -crf 25 \
  -pix_fmt yuv420p -movflags +faststart -an docs/demo/demo.mp4

# 4. GIFs (800 px, 12 fps): the end-user value scene and the architecture data flow
VF="fps=12,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
ffmpeg -y -ss 58.3 -t 12 -i docs/demo/source/renders/supply-chain-twin-master.mp4 -vf "$VF" -loop 0 docs/demo/demo.gif
ffmpeg -y -ss 34.0 -t 8 -i docs/demo/source/renders/supply-chain-twin-master.mp4 -vf "$VF" -loop 0 docs/demo/architecture.gif

# 5. audit report: slide stills, automated checks, AUDIT_REPORT.md/.docx/.pdf
python3 docs/demo/source/tools/build_audit_report.py docs/demo/source/audit/facts.yaml
```

`build_audit_report.py` exits with code 1 if any figure on screen no longer matches the repo outputs.
The simulation is seeded, so a fresh `make run` on the same code reproduces the outputs exactly; the
report lists which figures and slides each assumption affects (change-impact matrix).

The `renders/` folder (about 15 MB) is not committed; step 3 recreates it.
