#!/usr/bin/env python3
"""
Export HW2/README_QUALITY_CONTROL_PIPELINE.md to a print-friendly PDF.

Requirements (run once):
  pip install playwright markdown
  playwright install chromium

Optional: Node `npx` + `@mermaid-js/mermaid-cli` for static diagram rasterization
(if present, diagram is embedded as PNG for maximum fidelity).

Otherwise the diagram is rendered with Mermaid loaded from jsDelivr inside Chromium.

Usage:
  cd HW2 && source .venv/bin/activate && python scripts/export_qc_pipeline_readme_pdf.py
"""

from __future__ import annotations

import argparse
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

HW2_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MD = HW2_ROOT / "README_QUALITY_CONTROL_PIPELINE.md"
MERMAID_SLOT = "<!--__MERMAID_DIAGRAM_SLOT__-->"


def _mermaid_to_png_base64(mermaid_src: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="qc_pdf_") as td:
        mmd = Path(td) / "diagram.mmd"
        png = Path(td) / "diagram.png"
        mmd.write_text(mermaid_src.strip() + "\n", encoding="utf-8")
        try:
            subprocess.run(
                [
                    "npx",
                    "-y",
                    "@mermaid-js/mermaid-cli",
                    "-i",
                    str(mmd),
                    "-o",
                    str(png),
                    "-b",
                    "white",
                    "-w",
                    "2200",
                    "-H",
                    "1200",
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=str(HW2_ROOT),
                timeout=120,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if not png.is_file():
            return None
        return base64.standard_b64encode(png.read_bytes()).decode("ascii")


def _prepare_markdown(md: str) -> tuple[str, str | None]:
    """Return (markdown_with_slot, mermaid_source_or_none)."""
    pattern = re.compile(r"^```mermaid\n(.*?)```", re.DOTALL | re.MULTILINE)
    m = pattern.search(md)
    if not m:
        return md, None
    src = m.group(1).strip()
    replaced = pattern.sub("\n\n" + MERMAID_SLOT + "\n\n", md, count=1)
    return replaced, src


def _mermaid_block_html(mermaid_src: str) -> str:
    png_b64 = _mermaid_to_png_base64(mermaid_src)
    if png_b64:
        return (
            '<figure class="architecture-diagram">\n'
            f'  <img src="data:image/png;base64,{png_b64}" alt="QC pipeline architecture"/>\n'
            "  <figcaption>Figure — System architecture and data flow (§4).</figcaption>\n"
            "</figure>"
        )

    b64src = base64.standard_b64encode(mermaid_src.encode("utf-8")).decode("ascii")
    return f"""<figure class="architecture-diagram">
  <div id="mermaid-root"></div>
  <figcaption>Figure — System architecture and data flow (§4).</figcaption>
</figure>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
(async () => {{
  const src = atob("{b64src}");
  const root = document.getElementById("mermaid-root");
  const pre = document.createElement("pre");
  pre.className = "mermaid";
  pre.textContent = src;
  root.appendChild(pre);
  mermaid.initialize({{ startOnLoad: false, theme: "neutral", securityLevel: "loose" }});
  await mermaid.run({{ nodes: [pre] }});
}})();
</script>"""


CSS = """
@page { size: Letter; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 10.5pt;
  line-height: 1.48;
  color: #1a1a1a;
  max-width: 100%;
  word-wrap: break-word;
}
h1 { font-size: 19pt; margin: 0 0 0.45em; page-break-after: avoid; }
h2 {
  font-size: 12.8pt;
  margin: 1.25em 0 0.4em;
  page-break-after: avoid;
  border-bottom: 1px solid #d0d0d0;
  padding-bottom: 0.12em;
}
h3 { font-size: 11pt; margin: 1em 0 0.32em; page-break-after: avoid; }
p { margin: 0.5em 0; orphans: 3; widows: 3; }
ul, ol { margin: 0.45em 0 0.55em 1.2em; padding-left: 0.45em; }
li { margin: 0.22em 0; }
a { color: #0b57d0; text-decoration: none; word-break: break-word; }
hr { border: none; border-top: 1px solid #ddd; margin: 1.1em 0; }
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.65em 0 0.85em;
  font-size: 8.7pt;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td {
  border: 1px solid #bbb;
  padding: 5px 6px;
  vertical-align: top;
}
th { background: #efefef; font-weight: 600; }
code {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 9pt;
  background: #f4f4f4;
  padding: 1px 4px;
  border-radius: 2px;
  word-break: break-word;
}
pre {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 8.2pt;
  background: #f8f8f8;
  border: 1px solid #e6e6e6;
  padding: 9px 11px;
  border-radius: 3px;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: inherit; }
figure.architecture-diagram {
  margin: 12px 0 16px;
  page-break-inside: avoid;
  text-align: center;
}
figure.architecture-diagram img {
  max-width: 100%;
  height: auto;
}
figure.architecture-diagram svg {
  max-width: 100%;
  height: auto;
}
figure.architecture-diagram figcaption {
  font-size: 8.8pt;
  color: #555;
  margin-top: 7px;
}
strong { font-weight: 700; }
"""


def md_to_html(md_text: str) -> str:
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "markdown.extensions.nl2br"],
    )
    return body


def build_full_html(body_inner: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Quality Control Pipeline — Holistic Overview</title>
<style>{CSS}</style>
</head>
<body>
{body_inner}
</body>
</html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", type=Path, default=DEFAULT_MD)
    ap.add_argument(
        "--out",
        type=Path,
        default=HW2_ROOT / "out" / "README_QUALITY_CONTROL_PIPELINE.pdf",
    )
    args = ap.parse_args()
    md_path = args.readme.resolve()
    if not md_path.is_file():
        print(f"ERROR: not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    raw_md = md_path.read_text(encoding="utf-8")
    md_staged, mermaid_src = _prepare_markdown(raw_md)
    body_html = md_to_html(md_staged)
    if mermaid_src:
        body_html = body_html.replace(MERMAID_SLOT.strip(), _mermaid_block_html(mermaid_src))
    elif MERMAID_SLOT in body_html:
        body_html = body_html.replace(MERMAID_SLOT, "<p><em>(Diagram missing)</em></p>")

    html = build_full_html(body_html)

    out_path = args.out
    if not out_path.is_absolute():
        out_path = (HW2_ROOT / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        if mermaid_src and "mermaid.run" in html:
            try:
                page.wait_for_selector(".mermaid svg", timeout=30000)
            except Exception:
                print("WARNING: Mermaid SVG not detected in time; PDF may omit diagram.", file=sys.stderr)
        page.emulate_media(media="print")
        pdf_kwargs = dict(
            path=str(out_path),
            format="Letter",
            print_background=True,
            margin={"top": "11mm", "bottom": "11mm", "left": "11mm", "right": "11mm"},
            display_header_footer=False,
        )
        page.pdf(**pdf_kwargs)
        browser.close()

    print(f"Wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
