"""
Identify a PV module from its manufacturer datasheet PDF.

This is the alternative to supplying a pysam JSON. A pysam carries the module's
CEC parameters (from which we recover its name); a datasheet carries the NAME and
the nameplate specs directly, in prose and tables that vary wildly between
manufacturers.

Rather than trying to parse every vendor's spec-table layout, the primary strategy
is to identify the module against the CEC database we already ship and keep fresh
from NREL — if the datasheet names a module that exists there, we get the full,
authoritative parameter set for free. Regex spec-scraping is only the fallback.

WHAT A DATASHEET CANNOT GIVE: coordinates, GCR, modules-per-string, system size
and DC/AC ratio are properties of the *system design*, not the module. Only a
pysam (or the engineer) can supply those.
"""
from __future__ import annotations

import re

from . import cec_db

# Nameplate specs worth scraping when the module isn't in the CEC database.
# Each: (key, label regex, unit regex). Matched case-insensitively against text
# where the value may sit on the same line or the next one (tables flatten oddly).
_SPEC_PATTERNS = [
    ("pmax", r"(?:maximum\s+power|nominal\s+power|rated\s+power|p\s*max|pmpp|pmax)"
             r"[^0-9\n]{0,40}?(\d{2,4}(?:\.\d+)?)\s*(?:w|wp|watt)"),
    ("voc", r"(?:open[\s-]*circuit\s+voltage|voc)[^0-9\n]{0,40}?(\d{1,3}(?:\.\d+)?)\s*v"),
    ("isc", r"(?:short[\s-]*circuit\s+current|isc)[^0-9\n]{0,40}?(\d{1,3}(?:\.\d+)?)\s*a"),
    ("vmp", r"(?:voltage\s+at\s+(?:maximum|max)\s+power|vmpp?|v\s*mp)"
            r"[^0-9\n]{0,40}?(\d{1,3}(?:\.\d+)?)\s*v"),
    ("imp", r"(?:current\s+at\s+(?:maximum|max)\s+power|impp?|i\s*mp)"
            r"[^0-9\n]{0,40}?(\d{1,3}(?:\.\d+)?)\s*a"),
]

_NORM_RE = re.compile(r"[^A-Z0-9]+")
# Model tokens shorter than this match far too loosely (e.g. "G3", "M10").
_MIN_MODEL_LEN = 5


def _norm(s: str) -> str:
    """Uppercase, strip every non-alphanumeric — so 'Q.PRO-G3 245' and
    'QPRO G3-245' both become 'QPROG3245'."""
    return _NORM_RE.sub("", (s or "").upper())


def extract_text(path) -> str:
    """The PDF's text (first pages only — the identity and nameplate table are
    always at the front, and whole-document extraction is slow on big datasheets)."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - unreadable, treat as empty
            return ""
    out = []
    for page in reader.pages[:4]:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page shouldn't kill it
            continue
    return "\n".join(out)


def _cec_candidates() -> dict[str, list[tuple[str, str, dict]]]:
    """{normalised manufacturer: [(normalised model, display, params), …]}.

    Built from the same index cec_db uses for pysam matching, so it inherits the
    weekly NREL refresh and any custom modules an admin added.
    """
    by_mfr: dict[str, list[tuple[str, str, dict]]] = {}
    seen: set[str] = set()

    def add(display: str, params: dict) -> None:
        if display in seen:
            return
        seen.add(display)
        mfr, _, model = display.partition("—")
        nm, nmod = _norm(mfr), _norm(model)
        if not nmod:
            return
        by_mfr.setdefault(nm, []).append((nmod, display, params))

    for entry in cec_db.custom_modules():
        add(entry["display"], entry.get("params", {}))
    for bucket in cec_db._get_index().values():          # noqa: SLF001 - same package
        for display, params in bucket:
            add(display, params)
    return by_mfr


def identify(path) -> dict | None:
    """Identify the module described by a datasheet PDF.

    Returns None when the file yields no usable text at all. Otherwise a dict:
      display        "Manufacturer — Model", or "" if we couldn't name it
      manufacturer   / model
      source         "cec"  (matched the CEC database — params are authoritative)
                     "text" (scraped from the datasheet; specs only, name guessed)
      params         CEC parameter dict when source == "cec", else {}
      specs          {pmax, voc, isc, vmp, imp} scraped from the text (may be empty)
    """
    text = extract_text(path)
    if not len((text or "").strip()):
        return None
    norm_text = _norm(text)

    # ── 1. Identify against the CEC database (authoritative when it hits) ──
    best: tuple[int, str, dict] | None = None
    for nm, models in _cec_candidates().items():
        if len(nm) < 4 or nm not in norm_text:
            continue                      # manufacturer not mentioned — skip its models
        for nmod, display, params in models:
            if len(nmod) < _MIN_MODEL_LEN or nmod not in norm_text:
                continue
            # Longest model wins: "QPROG3245" beats a looser "QPROG3".
            if best is None or len(nmod) > best[0]:
                best = (len(nmod), display, params)

    specs = _scrape_specs(text)
    if best is not None:
        mfr, _, model = best[1].partition("—")
        return {"display": best[1], "manufacturer": mfr.strip(), "model": model.strip(),
                "source": "cec", "params": best[2], "specs": specs}

    # ── 2. Fall back to whatever the datasheet itself states ──
    mfr, model = _guess_name(text)
    display = f"{mfr} — {model}" if mfr and model else (model or mfr or "")
    return {"display": display, "manufacturer": mfr, "model": model,
            "source": "text", "params": {}, "specs": specs}


def _scrape_specs(text: str) -> dict:
    """Nameplate values stated on the datasheet (best-effort)."""
    out: dict[str, float] = {}
    flat = re.sub(r"[ \t]+", " ", text)
    for key, pattern in _SPEC_PATTERNS:
        m = re.search(pattern, flat, re.IGNORECASE)
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                continue
    return out


def _guess_name(text: str) -> tuple[str, str]:
    """Manufacturer / model from the front matter, when CEC didn't recognise it.

    Datasheets lead with the brand and the model designation, so the first few
    non-empty lines are the best available signal. Deliberately conservative — a
    wrong guess is worse than an empty field the engineer fills in.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:12]
    model = ""
    for ln in lines:
        # A model designation: has digits, is short, isn't a sentence or a spec row.
        if (any(c.isdigit() for c in ln) and 3 <= len(ln) <= 48
                and not re.search(r"\b(voltage|current|power|temperature|warranty|www|http|©)\b",
                                  ln, re.IGNORECASE)):
            model = ln
            break
    mfr = lines[0] if lines and lines[0] != model and len(lines[0]) <= 48 else ""
    return mfr, model
