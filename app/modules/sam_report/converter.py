"""
DOCX -> PDF conversion.

Prefers LibreOffice (`soffice`), which is the right choice on Azure Linux and
also updates Word fields (TOC page numbers, PAGE) during conversion. Falls back
to Microsoft Word via COM on a Windows box that has Word but not LibreOffice
(handy for local dev). Both run in a subprocess so they're isolated from the
web worker.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

# Common LibreOffice locations to probe beyond PATH.
_SOFFICE_CANDIDATES = [
    "soffice", "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice", "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
]


def _find_soffice() -> str | None:
    override = os.environ.get("SOFFICE_PATH")
    if override and Path(override).exists():
        return override
    for cand in _SOFFICE_CANDIDATES:
        found = shutil.which(cand) if os.path.basename(cand) == cand else (
            cand if Path(cand).exists() else None)
        if found:
            return found
    return None


# LibreOffice does NOT refresh a Word TOC (a "content index") on headless convert
# by default, so the generated PDF shows the field's placeholder run instead of
# real entries. Seeding this into the throwaway user profile flips on
# "update indexes + fields on load", so --convert-to produces a populated TOC.
_XCU_UPDATE_ON_LOAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<item oor:path="/org.openoffice.Office.Writer/Content/Update/Index">'
    '<prop oor:name="OnLoad" oor:op="fuse"><value>true</value></prop></item>'
    '<item oor:path="/org.openoffice.Office.Writer/Content/Update/Field">'
    '<prop oor:name="OnLoad" oor:op="fuse"><value>true</value></prop></item>'
    '</oor:items>'
)


def _convert_soffice(soffice: str, docx_path: Path, out_pdf: Path) -> None:
    with tempfile.TemporaryDirectory() as outdir:
        # A dedicated user profile avoids clashes when several workers convert at once.
        profile = Path(outdir) / "profile"
        user_dir = profile / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "registrymodifications.xcu").write_text(
            _XCU_UPDATE_ON_LOAD, encoding="utf-8")
        subprocess.run(
            [soffice, "--headless", "--norestore",
             f"-env:UserInstallation=file:///{profile.as_posix()}",
             "--convert-to", "pdf", "--outdir", outdir, str(docx_path)],
            check=True, capture_output=True, timeout=120,
        )
        produced = Path(outdir) / (docx_path.stem + ".pdf")
        if not produced.exists():
            raise RuntimeError("LibreOffice did not produce a PDF")
        shutil.move(str(produced), str(out_pdf))


def _ps_quote(path: Path) -> str:
    """Escape a path for a PowerShell single-quoted literal (double the quotes)."""
    return str(path).replace("'", "''")


def _convert_word(docx_path: Path, out_pdf: Path) -> None:
    """Windows-only fallback via Word COM, driven by a PowerShell subprocess."""
    docx_q = _ps_quote(docx_path)
    pdf_q = _ps_quote(out_pdf)
    ps = (
        "$w=New-Object -ComObject Word.Application; $w.Visible=$false; "
        f"$d=$w.Documents.Open('{docx_q}',$false,$true); "
        "try { $d.Fields.Update() | Out-Null; "
        "if($d.TablesOfContents.Count -gt 0){$d.TablesOfContents.Item(1).Update()} } catch {} ; "
        f"$d.SaveAs([ref]'{pdf_q}',[ref]17); $d.Close($false); $w.Quit()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, capture_output=True, timeout=180)
    if not out_pdf.exists():
        raise RuntimeError("Word did not produce a PDF")


def validate_pdf(path: str | Path) -> int | None:
    """Return the page count if `path` is a readable PDF we can append, else None.

    Rejects password-protected PDFs that don't open with an empty password (those
    can't be merged into the report). Used to reject bad datasheet uploads early,
    so append_pdfs at generate time only ever sees known-good files.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - needs a real password -> not usable
                return None
        return len(reader.pages)
    except (PdfReadError, OSError, ValueError):
        return None


def append_pdfs(base_pdf: str | Path, extra_pdfs: list[Path], out_pdf: str | Path) -> Path:
    """Write out_pdf = base_pdf followed by each extra PDF, in order."""
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    writer.append(str(base_pdf))
    for p in extra_pdfs:
        reader = PdfReader(str(p))
        if reader.is_encrypted:
            reader.decrypt("")
        writer.append(reader)
    with open(out_pdf, "wb") as fh:
        writer.write(fh)
    writer.close()
    return Path(out_pdf)


def docx_to_pdf(docx_path: str | Path, out_pdf: str | Path) -> Path:
    docx_path, out_pdf = Path(docx_path), Path(out_pdf)
    soffice = _find_soffice()
    if soffice:
        _convert_soffice(soffice, docx_path, out_pdf)
    elif platform.system() == "Windows":
        _convert_word(docx_path, out_pdf)
    else:
        raise RuntimeError(
            "No DOCX->PDF converter found. Install LibreOffice (soffice) — "
            "`playwright`/Chromium is not used for the .docx pipeline."
        )
    return out_pdf
