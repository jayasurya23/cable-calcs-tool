"""
CEC module database — recover a module's Manufacturer / Model from a pysam's CEC
6-parameter model, since the pysam itself carries no product name (SAM references
CEC modules by index and only exports the parameters).

Sources, highest precedence first:
  1. custom modules an engineer added   ({data}/cec/custom_modules.json)
  2. the cached NREL download            ({data}/cec/CEC_Modules.csv)
  3. the bundled offline fallback        (assets/CEC_Modules.csv)

The cached copy auto-refreshes from NREL's official SAM library (GitHub) when it
is stale (> 7 days) — non-blocking, and it falls back to the bundled copy if the
download fails or the network is unavailable.
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from app.config import settings

# NREL's official SAM library (same file SAM ships). GitHub raw — a fixed,
# non-spoofable host; do NOT swap this for any look-alike domain.
NREL_MODULES_URL = ("https://raw.githubusercontent.com/NREL/SAM/develop/"
                    "deploy/libraries/CEC%20Modules.csv")
_BUNDLED_CSV = Path(__file__).parent / "assets" / "CEC_Modules.csv"
_MAX_AGE_SECONDS = 7 * 24 * 3600  # refresh the cache weekly

# The CEC 6-parameter model + STC refs — together these uniquely fingerprint a
# module. They come straight from the DB, so a match against the DB is exact.
_MATCH_KEYS = ["N_s", "I_sc_ref", "V_oc_ref", "I_mp_ref", "V_mp_ref",
               "a_ref", "I_o_ref", "R_s", "R_sh_ref", "Adjust"]
_PYSAM_KEYS = {
    "N_s": "cec_n_s", "I_sc_ref": "cec_i_sc_ref", "V_oc_ref": "cec_v_oc_ref",
    "I_mp_ref": "cec_i_mp_ref", "V_mp_ref": "cec_v_mp_ref", "a_ref": "cec_a_ref",
    "I_o_ref": "cec_i_o_ref", "R_s": "cec_r_s", "R_sh_ref": "cec_r_sh_ref",
    "Adjust": "cec_adjust",
}

_lock = threading.Lock()
# Guards read-modify-write of custom_modules.json. In-process only: with several
# gunicorn workers two simultaneous edits could still race, so writes are atomic
# (temp + os.replace) and the file is never left half-written.
_custom_lock = threading.RLock()
_cache: dict | None = None          # {"index": {...}, "source": str, "mtime": float}
_refreshing = False


# ─── storage paths ───────────────────────────────────────────────────────────

def _cec_dir() -> Path:
    d = Path(settings.data_dir) / "cec"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_csv() -> Path:
    return _cec_dir() / "CEC_Modules.csv"


def _custom_json() -> Path:
    return _cec_dir() / "custom_modules.json"


# ─── parsing + indexing ──────────────────────────────────────────────────────

def _display(manufacturer: str, name: str) -> str:
    """'Manufacturer — Model' (the DB Name already carries the manufacturer as a
    prefix, so strip it to get the bare model)."""
    manufacturer = (manufacturer or "").strip()
    name = (name or "").strip()
    model = name[len(manufacturer):].strip(" -–—") if manufacturer and name.startswith(manufacturer) else name
    return f"{manufacturer} — {model}" if manufacturer and model else (manufacturer or model or name)


def _round_key(p: dict) -> tuple:
    return (round(p["V_mp_ref"], 2), round(p["I_mp_ref"], 2),
            round(p["V_oc_ref"], 2), round(p["I_sc_ref"], 2), int(round(p["N_s"])))


def _entry_key(entry: dict) -> tuple | None:
    """The match key of a stored custom entry, or None when it has no CEC
    parameter set.

    Datasheet-derived entries are keyed on nameplate values and carry
    `params == {}`, so calling _round_key on them raises KeyError. Every loop
    over the custom list must go through here: a single such entry used to make
    lookup_module_name blow up for EVERY report, not just its own.
    """
    params = entry.get("params") or {}
    try:
        return _round_key(params)
    except (KeyError, TypeError, ValueError):
        return None


def _parse_csv(path: Path) -> dict:
    """Index the CEC CSV -> {rounded-key: [(display, params), …]}. The NREL CSV
    has 3 header rows (names / units / SAM-var map), then data."""
    index: dict[tuple, list] = {}
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return index
    if len(rows) < 4:
        return index
    hdr = rows[0]
    ci = {c: i for i, c in enumerate(hdr)}
    if any(c not in ci for c in ["Name", "Manufacturer", *_MATCH_KEYS]):
        return index
    for r in rows[3:]:
        if len(r) < len(hdr):
            continue
        try:
            params = {k: float(r[ci[k]]) for k in _MATCH_KEYS}
        except (ValueError, IndexError):
            continue
        disp = _display(r[ci["Manufacturer"]], r[ci["Name"]])
        index.setdefault(_round_key(params), []).append((disp, params))
    return index


class LibraryUnavailable(RuntimeError):
    """The custom-module file exists but could not be read this time."""


def _load_custom(strict: bool = False) -> list[dict]:
    """The engineer-maintained module list.

    `strict` decides what an unreadable file means. On a READ path (naming a
    module for a report) the answer is "carry on without custom names" — a
    transient SMB blip on the Azure Files mount must not fail a report. On a
    WRITE path it must raise: treating a failed read as "the library is empty"
    and then saving would persist that emptiness, destroying every entry the
    engineers had added.
    """
    p = _custom_json()
    if not p.is_file():
        return []                       # genuinely empty — safe to write over
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        if strict:
            raise LibraryUnavailable(
                "The module library file could not be read, so it can't be "
                "changed right now without risking the entries already in it. "
                "Try again in a moment.") from exc
        return []
    return data if isinstance(data, list) else []


def _active_source() -> Path:
    """Cached NREL copy if present, else the bundled fallback."""
    c = _cached_csv()
    return c if c.is_file() else _BUNDLED_CSV


def _get_index() -> dict:
    """The parsed base index (cached in memory; rebuilt when the source changes)."""
    global _cache
    src = _active_source()
    mtime = src.stat().st_mtime if src.is_file() else 0.0
    with _lock:
        if _cache is None or _cache["source"] != str(src) or _cache["mtime"] != mtime:
            _cache = {"index": _parse_csv(src), "source": str(src), "mtime": mtime}
        return _cache["index"]


# ─── matching ────────────────────────────────────────────────────────────────

def _pysam_params(pysam_data: dict) -> dict | None:
    out = {}
    for k, src in _PYSAM_KEYS.items():
        v = pysam_data.get(src)
        if v is None:
            return None
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            return None
    return out


def _best(candidates: list, params: dict) -> str | None:
    """Among same-rounded-key candidates, the closest on the full parameter set.
    Prefer the plainer name (no ' BFR ' bifacial-variant tag) on ties."""
    if not candidates:
        return None
    def dist(c):
        return sum(abs(c[1][k] - params[k]) for k in _MATCH_KEYS)
    candidates = sorted(candidates, key=lambda c: (dist(c), " BFR " in f" {c[0]} ", len(c[0])))
    return candidates[0][0]


def lookup_module_name(pysam_data: dict) -> str | None:
    """'Manufacturer — Model' for the module described by this pysam, or None if
    it isn't a CEC-database module or no match is found. Custom modules win."""
    params = _pysam_params(pysam_data)
    if params is None:
        return None
    key = _round_key(params)
    # 1. custom modules first
    custom_hits = [(c["display"], c["params"]) for c in _load_custom()
                   if _entry_key(c) == key]
    if custom_hits:
        return _best(custom_hits, params)
    # 2. the base DB (exact rounded key)
    hit = _best(_get_index().get(key, []), params)
    if hit:
        return hit
    # 3. tolerant fallback: nearest on V/I refs within a tight tolerance
    best_disp, best_d = None, None
    for cands in _get_index().values():
        for disp, p in cands:
            if (abs(p["V_mp_ref"] - params["V_mp_ref"]) <= 0.05
                    and abs(p["I_mp_ref"] - params["I_mp_ref"]) <= 0.05
                    and abs(p["V_oc_ref"] - params["V_oc_ref"]) <= 0.1
                    and abs(p["I_sc_ref"] - params["I_sc_ref"]) <= 0.05
                    and int(round(p["N_s"])) == int(round(params["N_s"]))):
                d = sum(abs(p[k] - params[k]) for k in _MATCH_KEYS)
                if best_d is None or d < best_d:
                    best_disp, best_d = disp, d
    return best_disp


# ─── custom modules ──────────────────────────────────────────────────────────

def _save_custom(entries: list[dict]) -> None:
    """Write the list atomically under a name no other writer can collide with.

    A single shared "custom_modules.json.tmp" was not safe: the app runs two
    gunicorn workers across up to three replicas, all writing the same Azure
    Files share, so two overlapping saves interleaved their bytes in one temp
    file and then both renamed it over the real one. Per-writer temp names plus
    os.replace make each save all-or-nothing.
    """
    path = _custom_json()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)     # never leave a half-written temp behind
        raise


def _ensure_ids(entries: list[dict]) -> bool:
    """Give every entry a stable id. Entries were originally addressed by list
    position, which breaks the moment two people edit at once (or one deletes
    while another is on the edit form). Ids are assigned lazily on first read;
    returns True when something changed and the list needs writing back."""
    changed = False
    for e in entries:
        if not e.get("id"):
            e["id"] = uuid.uuid4().hex[:10]
            changed = True
        if not e.get("kind"):
            e["kind"] = "params"        # the original, pysam-parameter-keyed form
            changed = True
    return changed


def custom_modules() -> list[dict]:
    """Every engineer-added module, each with a stable `id` and a `kind`."""
    with _custom_lock:
        try:
            entries = _load_custom(strict=True)
        except LibraryUnavailable:
            return []                   # display-only caller: show nothing, write nothing
        if _ensure_ids(entries):
            _save_custom(entries)
        return entries


def get_custom_module(module_id: str) -> dict | None:
    for e in custom_modules():
        if e.get("id") == module_id:
            return e
    return None


def _compose(manufacturer: str, model: str) -> tuple[str, str, str]:
    manufacturer, model = (manufacturer or "").strip(), (model or "").strip()
    if not model:
        raise ValueError("Enter the module model name.")
    return manufacturer, model, (f"{manufacturer} — {model}" if manufacturer else model)


def add_custom_module(manufacturer: str, model: str, pysam_data: dict) -> str:
    """Remember a module by its pysam params under 'Manufacturer — Model'. Future
    reports whose module matches these params auto-fill this name."""
    params = _pysam_params(pysam_data)
    if params is None:
        raise ValueError("That pysam JSON has no CEC module parameters to match on.")
    manufacturer, model, display = _compose(manufacturer, model)
    with _custom_lock:
        entries = _load_custom(strict=True)
        _ensure_ids(entries)
        key = _round_key(params)
        entries = [e for e in entries if _entry_key(e) != key]
        entries.append({"id": uuid.uuid4().hex[:10], "display": display,
                        "manufacturer": manufacturer, "model": model,
                        "kind": "params", "params": params, "specs": {}})
        _save_custom(entries)
    return display


def update_custom_module(module_id: str, manufacturer: str, model: str,
                         specs: dict | None = None, pmax_w=None,
                         notes: str = "") -> str:
    """Edit an existing custom module in place, keeping its id.

    The name can always be corrected. The nameplate values can be corrected too,
    but ONLY for a specs-keyed entry: for a params-keyed one the CEC 6-parameter
    set is the match key and came from a pysam, so there is nothing meaningful to
    hand-edit — changing it by hand would silently break matching.
    """
    manufacturer, model, display = _compose(manufacturer, model)
    with _custom_lock:
        entries = _load_custom(strict=True)
        _ensure_ids(entries)
        for e in entries:
            if e.get("id") != module_id:
                continue
            e["manufacturer"], e["model"], e["display"] = manufacturer, model, display
            e["notes"] = (notes or "").strip()
            if pmax_w not in (None, ""):
                n = _number(pmax_w, 100_000)     # W; a module is ~200-800
                if n is not None:
                    e["pmax_w"] = n
            if specs is not None and e.get("kind") == "specs":
                wanted = _wanted_specs(specs)
                if len(wanted) < 3:
                    raise ValueError(
                        "Keep at least three of Voc, Isc, Vmp and Imp — they are "
                        "what identifies this module on a later upload.")
                e["specs"] = wanted
            _save_custom(entries)
            return display
    raise ValueError("That module is no longer in the library — it may have been removed.")


def remove_custom_module(module_id: str) -> bool:
    """Delete by stable id. Returns False if it was already gone."""
    with _custom_lock:
        entries = _load_custom(strict=True)
        _ensure_ids(entries)
        keep = [e for e in entries if e.get("id") != module_id]
        if len(keep) == len(entries):
            return False
        _save_custom(keep)
        return True


# ─── refresh from NREL ───────────────────────────────────────────────────────

def _cache_age_seconds() -> float | None:
    c = _cached_csv()
    return (time.time() - c.stat().st_mtime) if c.is_file() else None


def refresh_from_nrel(timeout: int = 60) -> tuple[bool, str]:
    """Download the latest CEC modules CSV from NREL into the /data cache.
    Returns (ok, message). Validates it parses to a non-trivial table first."""
    tmp = _cached_csv().with_suffix(".csv.tmp")
    try:
        req = urllib.request.Request(NREL_MODULES_URL, headers={"User-Agent": "cable-web"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed HTTPS host
            data = resp.read()
        tmp.write_bytes(data)
        parsed = _parse_csv(tmp)
        if len(parsed) < 100:                     # sanity: real DB has thousands of keys
            tmp.unlink(missing_ok=True)
            return False, "Downloaded file didn't look like the CEC module database."
        tmp.replace(_cached_csv())
        global _cache
        with _lock:
            _cache = None                          # force re-index from the new file
        return True, f"Updated from NREL ({len(data) // 1024} KB)."
    except Exception as exc:  # noqa: BLE001 - network/parse errors all fall back
        tmp.unlink(missing_ok=True)
        return False, f"Could not refresh from NREL: {exc}"


def maybe_refresh_async() -> None:
    """If the cache is missing or stale, refresh in a background thread so the
    request that triggered it isn't blocked on a download."""
    global _refreshing
    age = _cache_age_seconds()
    if age is not None and age < _MAX_AGE_SECONDS:
        return
    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def _run():
        global _refreshing
        try:
            refresh_from_nrel()
        finally:
            _refreshing = False

    threading.Thread(target=_run, name="cec-refresh", daemon=True).start()


def stats() -> dict:
    src = _active_source()
    age = _cache_age_seconds()
    return {
        "module_count": sum(len(v) for v in _get_index().values()),
        "custom_count": len(_load_custom()),
        "source": "NREL cache" if src == _cached_csv() else "bundled",
        "cache_age_days": (round(age / 86400, 1) if age is not None else None),
    }


# ─── browsing the database ───────────────────────────────────────────────────

_flat_cache: dict | None = None


def _flat() -> list[tuple]:
    """The whole database as a flat, name-sorted list for browsing/searching.

    Built once per source file and memoised alongside the match index — the CSV
    is ~30k rows, so rebuilding it per keystroke would be wasteful, while a
    single pass over the cached list is instant.
    Each item: (haystack, display, manufacturer, model, params).
    """
    global _flat_cache
    src = _active_source()
    mtime = src.stat().st_mtime if src.is_file() else 0.0
    with _lock:
        if _flat_cache is not None and _flat_cache["mtime"] == mtime                 and _flat_cache["source"] == str(src):
            return _flat_cache["rows"]

    rows = []
    for bucket in _get_index().values():
        for display, params in bucket:
            mfr, _, model = display.partition("—")
            rows.append((display.lower(), display, mfr.strip(), model.strip(), params))
    rows.sort(key=lambda r: r[0])

    with _lock:
        _flat_cache = {"rows": rows, "source": str(src), "mtime": mtime}
    return rows


def _row_view(display: str, mfr: str, model: str, params: dict) -> dict:
    """One database row shaped for the browse table."""
    vmp, imp = params.get("V_mp_ref"), params.get("I_mp_ref")
    return {
        "display": display, "manufacturer": mfr, "model": model,
        "voc": params.get("V_oc_ref"), "isc": params.get("I_sc_ref"),
        "vmp": vmp, "imp": imp,
        "cells": int(params["N_s"]) if params.get("N_s") else None,
        "pmax": (round(vmp * imp) if vmp and imp else None),
    }


def search_modules(query: str, limit: int = 50) -> dict:
    """Name search across the CEC database.

    Every whitespace-separated term must appear, so "qcells 400" narrows the way
    a person expects rather than returning everything matching either word.
    """
    terms = [t for t in (query or "").lower().split() if t]
    rows = _flat()
    if not terms:
        return {"rows": [], "total": len(rows), "matched": 0, "query": "", "truncated": False}

    hits = [r for r in rows if all(t in r[0] for t in terms)]
    shown = hits[:limit]
    return {
        "rows": [_row_view(d, mf, mo, pa) for _, d, mf, mo, pa in shown],
        "total": len(rows), "matched": len(hits), "query": query.strip(),
        "truncated": len(hits) > limit,
    }


# ─── identify from nameplate specs (no name needed) ──────────────────────────

# Datasheets round their published values, so match on a tolerance rather than
# equality. Deliberately tight: a false match is worse than no match.
_SPEC_TOL = {"V_oc_ref": 0.30, "I_sc_ref": 0.15, "V_mp_ref": 0.30, "I_mp_ref": 0.15}
_SPEC_FROM_SHEET = {"voc": "V_oc_ref", "isc": "I_sc_ref",
                    "vmp": "V_mp_ref", "imp": "I_mp_ref"}
# The datasheet-facing names, in the order an engineer reads them off a sheet.
SPEC_FIELDS = [("voc", "Voc", "V"), ("isc", "Isc", "A"),
               ("vmp", "Vmp", "V"), ("imp", "Imp", "A")]


def _number(value, limit: float):
    """A finite, sane number, or None. Anything else is discarded.

    json.dumps happily writes Infinity/NaN, which are not valid JSON — one
    "1e400" typed into a number field would be written out and then fail to
    parse forever after, taking the whole library page down with it.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):    # NaN / +-inf
        return None
    return f if 0 <= f <= limit else None


def _wanted_specs(specs: dict) -> dict:
    """Datasheet keys (voc/isc/vmp/imp) -> the CEC names we match on."""
    out = {}
    for k, v in (specs or {}).items():
        if k not in _SPEC_FROM_SHEET or v in (None, ""):
            continue
        n = _number(v, 10_000)          # no PV module exceeds this in V or A
        if n is not None:
            out[_SPEC_FROM_SHEET[k]] = n
    return out


def specs_for_display(entry: dict) -> list[dict]:
    """A custom entry's nameplate values in datasheet terms, for the edit form."""
    saved = entry.get("specs") or {}
    return [{"key": key, "label": label, "unit": unit,
             "value": saved.get(_SPEC_FROM_SHEET[key])}
            for key, label, unit in SPEC_FIELDS]



def lookup_by_specs(specs: dict, text_hint: str = "") -> dict | None:
    """Identify a module from its nameplate values alone.

    A datasheet always states Voc / Isc / Vmp / Imp even when its layout defeats
    name extraction, and those four are most of what fingerprints a module in the
    CEC database — so the numbers can identify it when the text cannot.

    Returns {"display", "params", "confident"} or None. `confident` is False when
    several different modules match the same numbers (common for one panel sold
    under several brand names), in which case the caller should ask rather than
    assume.
    """
    wanted = {_SPEC_FROM_SHEET[k]: float(v) for k, v in (specs or {}).items()
              if k in _SPEC_FROM_SHEET and v is not None}
    if len(wanted) < 3:                    # too little to discriminate on
        return None

    # An engineer already verified this module -> that answer wins outright.
    verified = _custom_spec_match(wanted)
    if verified:
        return verified

    hits: list[tuple[float, str, dict]] = []
    for bucket in _get_index().values():
        for display, params in bucket:
            dist = 0.0
            for key, target in wanted.items():
                delta = abs(params[key] - target)
                if delta > _SPEC_TOL[key]:
                    break
                dist += delta
            else:
                hits.append((dist, display, params))
    if not hits:
        return None

    # Nameplate values alone are NOT unique: commodity 60-cell panels from
    # different manufacturers publish near-identical Voc/Isc/Vmp/Imp. Measured on
    # the real database, one set of numbers matched Qcells, AXITEC, BYD and
    # Centrosolar. So the specs narrow the field; the manufacturer named in the
    # datasheet text is what settles it.
    hint = _norm_text(text_hint)
    if hint:
        branded = [h for h in hits if _mfr_of(h[1]) and _norm_text(_mfr_of(h[1])) in hint]
        if branded:
            hits = branded

    hits.sort(key=lambda h: (h[0], len(h[1])))
    best_dist, best_display, best_params = hits[0]
    names = {d for _, d, _ in hits}
    # The risk being guarded against is attaching the WRONG MANUFACTURER. Several
    # remaining entries from the same maker are usually naming variants of one
    # physical panel (e.g. "Q.PRO-G3 245" / "Q.PRO BFR-G3 245"), which is a safe
    # pick; several different makers is not.
    makers = {_mfr_of(d) for d in names}
    return {"display": best_display, "params": best_params,
            "confident": len(makers) == 1, "alternatives": sorted(names)[:6]}


def _norm_text(s: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _mfr_of(display: str) -> str:
    return display.partition("—")[0].strip()


def add_custom_module_from_specs(manufacturer: str, model: str, specs: dict,
                                 source: str = "") -> str:
    """Remember a module the CEC database doesn't carry, keyed on the nameplate
    values read from its datasheet, under an engineer-verified name.

    This closes the loop for brand-new panels: the datasheet gives the numbers,
    the engineer confirms the name, and every later upload of that module is
    identified automatically. Because a person verified it, a match here is
    treated as authoritative even though nameplate values alone are otherwise
    ambiguous (several makers publish identical ones).
    """
    wanted = _wanted_specs(specs)
    if len(wanted) < 3:
        raise ValueError("That datasheet didn't yield enough nameplate values "
                         "(need at least three of Voc, Isc, Vmp, Imp) to identify "
                         "the module later.")
    manufacturer, model, display = _compose(manufacturer, model)

    entry = {"id": uuid.uuid4().hex[:10], "display": display,
             "manufacturer": manufacturer, "model": model,
             "kind": "specs", "specs": wanted, "params": {},
             "source": (source or "datasheet")}
    pmax = _number((specs or {}).get("pmax"), 100_000)
    if pmax is not None:
        entry["pmax_w"] = pmax

    with _custom_lock:
        entries = _load_custom(strict=True)
        _ensure_ids(entries)
        entries = [e for e in entries
                   if not (e.get("kind") == "specs" and e.get("specs") == wanted)]
        entries.append(entry)
        _save_custom(entries)
    return display


def _custom_spec_match(wanted: dict) -> dict | None:
    """A verified custom module whose nameplate values match. Engineer-confirmed,
    so it outranks anything inferred from the CEC database.

    Compares only the values BOTH sides carry. The two sides routinely disagree
    on which they have: a stored entry may hold three (that is all its datasheet
    published, and the editor permits three), while a fresh scrape may recover
    all four. Requiring the saved entry to answer every key the datasheet offers
    made such an entry permanently unmatchable — and silently so, because
    lookup_by_specs then fell through to the CEC database and returned a
    DIFFERENT manufacturer's lookalike panel into the report. Three shared
    values is the floor, the same bar add/update enforce.
    """
    for e in _load_custom():
        if e.get("kind") != "specs":
            continue
        saved = e.get("specs") or {}
        shared = [k for k in saved if k in wanted and k in _SPEC_TOL]
        if len(shared) < 3:
            continue
        if all(abs(saved[k] - wanted[k]) <= _SPEC_TOL[k] for k in shared):
            return {"display": e["display"], "params": {}, "confident": True,
                    "alternatives": [], "custom": True}
    return None
