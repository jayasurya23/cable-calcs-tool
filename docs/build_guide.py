"""Render docs/INTERN_GUIDE.md to a print-ready PDF.

    python docs/build_guide.py [output-dir]

Two LibreOffice quirks drive the odd-looking choices here; both were found by
rendering and inspecting the output rather than from the docs:

  * Its HTML import ignores CSS table-layout/width, so column widths must be an
    explicit <colgroup> — without it the first column collapses and breaks words
    mid-word ("Analysi/s").
  * It ignores BOTH the CSS width and the width= attribute on images, placing
    them at native pixel size (96 dpi) and letting the page clip the overflow.
    So the PNGs must already be the printed size: 660 px ≈ 175 mm on A4.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
IMG_PRINT_WIDTH_PX = 660
SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "soffice", "libreoffice",
]

CSS = """
  @page { size: A4; margin: 15mm 14mm; }
  body { font-family: "Jost", "Segoe UI", Arial, sans-serif; font-size: 10pt;
         line-height: 1.3; color: #1a1a1a; }
  h1 { font-family: "Jost"; font-size: 21pt; font-weight: 600; color: #A32035;
       margin: 0 0 1mm; border-bottom: 2px solid #A32035; padding-bottom: 2mm; }
  h2 { font-family: "Jost"; font-size: 12.5pt; font-weight: 600; color: #A32035;
       margin: 5mm 0 1.5mm; page-break-after: avoid; }
  p { margin: 1.6mm 0; }
  ul, ol { margin: 1.6mm 0 1.6mm 5mm; padding-left: 3mm; }
  li { margin-bottom: 0.8mm; }
  table { border-collapse: collapse; margin: 2mm 0 3mm; border-color: #c7c7c7; }
  th, td { font-family: "Jost"; font-size: 9pt; vertical-align: top; }
  th { background: #f2e8ea; color: #6d1622; text-align: left; font-weight: 600; }
  code { font-family: Consolas, monospace; font-size: 9pt; background: #f2f2f2; }
  blockquote { margin: 2.5mm 0; padding: 2mm 3.5mm; background: #fdf6e6;
               border-left: 3pt solid #C8A032; }
  blockquote p { margin: 0; }
  hr { border: 0; border-top: 0.6pt solid #ddd; margin: 4mm 0; }
  .shot { margin: 3mm 0 4mm; }
  .cap { font-family: "Jost"; font-size: 8pt; color: #666; }
"""


def build(out_dir: Path) -> Path:
    import markdown
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "img").mkdir(exist_ok=True)

    # Screenshots must ship at their printed pixel size (see module docstring).
    try:
        from PIL import Image
        for src in sorted((DOCS / "img").glob("*.png")):
            im = Image.open(src).convert("RGB")
            if im.width != IMG_PRINT_WIDTH_PX:
                h = round(im.height * IMG_PRINT_WIDTH_PX / im.width)
                im = im.resize((IMG_PRINT_WIDTH_PX, h), Image.LANCZOS)
            im.save(out_dir / "img" / src.name, optimize=True)
    except ImportError:                      # Pillow absent: use them as-is
        for src in sorted((DOCS / "img").glob("*.png")):
            (out_dir / "img" / src.name).write_bytes(src.read_bytes())

    body = markdown.markdown((DOCS / "INTERN_GUIDE.md").read_text(encoding="utf-8"),
                             extensions=["tables", "sane_lists", "fenced_code"])
    body = body.replace("<table>",
                        '<table border="1" cellspacing="0" cellpadding="5" width="100%">')
    body = body.replace("<thead>",
                        '<colgroup><col width="26%"/><col width="74%"/></colgroup><thead>')
    body = re.sub(r'<p><img alt="([^"]*)" src="([^"]*)" /></p>',
                  r'<p class="shot"><img src="\2"/><br/><span class="cap">\1</span></p>',
                  body)

    html_path = out_dir / "Cable Web - Quick Start Guide.html"
    html_path.write_text(
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style>'
        f"</head><body>{body}</body></html>", encoding="utf-8")

    for exe in SOFFICE_CANDIDATES:
        try:
            subprocess.run([exe, "--headless", "--convert-to", "pdf",
                            "--outdir", str(out_dir), str(html_path)],
                           check=True, capture_output=True, timeout=180)
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    else:
        raise SystemExit("LibreOffice (soffice) not found — needed to render the PDF.")

    pdf = html_path.with_suffix(".pdf")
    if not pdf.is_file():
        raise SystemExit("LibreOffice did not produce a PDF.")
    return pdf


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else DOCS / "_build"
    print("Wrote", build(dest))
