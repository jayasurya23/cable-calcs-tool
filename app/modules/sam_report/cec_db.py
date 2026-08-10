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
import threading
import time
import urllib.request
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


def _load_custom() -> list[dict]:
    p = _custom_json()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
    return []


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
                   if _round_key(c["params"]) == key]
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

def add_custom_module(manufacturer: str, model: str, pysam_data: dict) -> str:
    """Remember a module by its pysam params under 'Manufacturer — Model'. Future
    reports whose module matches these params auto-fill this name."""
    params = _pysam_params(pysam_data)
    if params is None:
        raise ValueError("That pysam JSON has no CEC module parameters to match on.")
    manufacturer, model = manufacturer.strip(), model.strip()
    if not model:
        raise ValueError("Enter the module model name.")
    display = f"{manufacturer} — {model}" if manufacturer else model
    entries = _load_custom()
    key = _round_key(params)
    entries = [e for e in entries if _round_key(e["params"]) != key]  # replace same-key
    entries.append({"display": display, "manufacturer": manufacturer,
                    "model": model, "params": params})
    _custom_json().write_text(json.dumps(entries), encoding="utf-8")
    return display


def custom_modules() -> list[dict]:
    return _load_custom()


def remove_custom_module(index: int) -> None:
    entries = _load_custom()
    if 0 <= index < len(entries):
        entries.pop(index)
        _custom_json().write_text(json.dumps(entries), encoding="utf-8")


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
