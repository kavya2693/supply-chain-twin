"""Inline an exported JSON file into a composition (renders cannot fetch files).

Replaces the block `/* NAME:BEGIN */ ... /* NAME:END */` with `const NAME = <json>;`.

Usage:
    python3 tools/inject_sheet.py assets/change_sheet.json compositions/value.html --name SHEET
    python3 tools/inject_sheet.py assets/search_walk.json compositions/s04-routes.html --name WALK
"""

import argparse
import json
import re

ap = argparse.ArgumentParser()
ap.add_argument("data")
ap.add_argument("html")
ap.add_argument("--name", default="SHEET")
a = ap.parse_args()

with open(a.data) as fh:
    payload = json.load(fh)
with open(a.html) as fh:
    html = fh.read()
data = json.dumps(payload, separators=(",", ":"))
block = f"/* {a.name}:BEGIN */ const {a.name} = {data}; /* {a.name}:END */"
pattern = r"/\* " + a.name + r":BEGIN \*/.*?/\* " + a.name + r":END \*/"
html, n = re.subn(pattern, lambda _: block, html, flags=re.S)
assert n == 1, f"{a.name} marker not found in {a.html}"
with open(a.html, "w") as fh:
    fh.write(html)
print(f"injected {a.data} into {a.html} as {a.name} ({len(block):,} chars)")
