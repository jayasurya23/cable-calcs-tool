"""
Build a ReportContext from staged module workbooks + pysam + form data.

Auto-fill sources:
  * Excel  -> per-module rows (year, Max 3-hr-avg Isc, Max Voc), the year range,
    and the run count.
  * pysam  -> coordinates (from solar_resource_file), GCR (subarray1_gcr),
    modules-per-string (subarray1_modules_per_string), module wattage
    (Vmp*Imp from the active module parameter family).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import parser
from .report_models import ReportContext, ReportModule, ReportProject, ResultRow

_COORD_RE = re.compile(r"(-?\d{1,3}\.\d{3,})[_,\s]+(-?\d{1,3}\.\d{3,})")
_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]
# Active inverter's rated-AC-power key, by pvsamv1 inverter_model selector.
_INV_PACO_KEY = {"0": "inv_snl_paco", "1": "inv_ds_paco",
                 "2": "inv_pd_paco", "3": "inv_cec_cg_paco"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _equipment_label(eq_name: str | None, eq_type: str | None, specs: dict, key: str) -> str:
    """A CONCISE, editable equipment placeholder (fits one Project-Info cell):
    product name if present, else the model type (without its parenthetical) +
    the single most identifying spec. Full type + all specs stay in the
    Equipment card; the engineer replaces this with the real product name."""
    if eq_name:
        return eq_name
    base = eq_type.split("(")[0].strip() if eq_type else ""   # drop "(user-entered specs)" etc.
    spec = specs.get(key, "")
    if base and spec:
        return f"{base} — {spec}"
    return base or spec


def _albedo_text(albedo) -> str:
    """The albedo value sentence for the Inputs section (the template already
    carries the intro "Since bifacial modules… was carefully considered.")."""
    vals = [_num(a) for a in albedo] if isinstance(albedo, list) else []
    vals = [v for v in vals if v is not None]
    if len(vals) != 12:
        return ""
    if len(set(vals)) == 1:
        return f"A uniform albedo of {vals[0]:.2f} was applied for all months."
    parts = []
    for val in sorted(set(vals), reverse=True):
        months = [i for i, v in enumerate(vals) if v == val]
        parts.append(f"{val:.2f} for months of {_month_phrase(months)}")
    return "Albedo values of " + "; and ".join(parts) + " were applied."


def _month_phrase(idxs: list[int]) -> str:
    """'September through April' for a contiguous (possibly wrapping) run, else a list."""
    if len(idxs) == 1:
        return _MONTHS[idxs[0]]
    s = set(idxs)
    for start in range(12):
        run = [(start + k) % 12 for k in range(len(idxs))]
        if set(run) == s:
            return f"{_MONTHS[run[0]]} through {_MONTHS[run[-1]]}"
    return ", ".join(_MONTHS[i] for i in idxs)


def prefill_from_pysam(pysam_data: dict) -> dict:
    """Return Project-Information / Inputs values derived from pysam.

    Keys: coordinates, gcr, modules_per_string, module_wattage, module_model,
    inverter_model, dc_ac_ratio, system_size_dc, albedo_text. Missing -> "".
    """
    out = {"coordinates": "", "gcr": "", "modules_per_string": "", "module_wattage": None,
           "module_model": "", "inverter_model": "", "dc_ac_ratio": "",
           "system_size_dc": "", "albedo_text": "", "weather_file": ""}
    if not pysam_data:
        return out

    def get(key):
        for k, v in pysam_data.items():
            if k.lower() == key:
                return v
        return None

    srf = get("solar_resource_file")
    if isinstance(srf, str):
        m = _COORD_RE.search(srf)
        if m:
            out["coordinates"] = f"{m.group(1)}, {m.group(2)}"
        # The NSRDB weather-file name (basename of the full path) for the
        # Inputs "Weather Data:" line.
        name = srf.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if name:
            out["weather_file"] = name

    gcr = _num(get("subarray1_gcr"))
    if gcr is not None:
        out["gcr"] = f"{gcr:g}"

    mps = _num(get("subarray1_modules_per_string"))
    if mps is not None:
        out["modules_per_string"] = f"{int(mps)}"

    # System size (DC nameplate) + DC/AC ratio (DC ÷ total inverter AC).
    system_kw = _num(get("system_capacity"))
    if system_kw is not None:
        out["system_size_dc"] = f"{system_kw:.1f} kW"
    inv_sel = get("inverter_model")
    paco = _num(get(_INV_PACO_KEY.get(str(inv_sel), "")))
    count = _num(get("inverter_count")) or 1
    if system_kw and paco:
        ac_kw = paco * count / 1000.0
        if ac_kw:
            out["dc_ac_ratio"] = f"{system_kw / ac_kw:.2f}"

    # Albedo statement. When use_wf_albedo=1 SAM reads albedo from the weather
    # file and IGNORES the monthly `albedo` input array — so don't assert those
    # values; state the weather-file source instead.
    use_wf = _num(get("use_wf_albedo"))
    if use_wf is not None and int(use_wf) == 1:
        out["albedo_text"] = ("Ground albedo was taken from the weather file "
                              "(NSRDB) for each timestep.")
    else:
        out["albedo_text"] = _albedo_text(get("albedo"))

    # Module / inverter model + wattage from the equipment extraction.
    try:
        eq = parser.extract_equipment(pysam_data)
        pmax = eq.module_specs.get("Pmax")
        if pmax and (w := re.search(r"\d+", pmax)):
            out["module_wattage"] = int(w.group(0))
        out["module_model"] = _equipment_label(
            eq.module_model, eq.module_model_type, eq.module_specs, "Pmax")
        out["inverter_model"] = _equipment_label(
            eq.inverter_model, eq.inverter_model_type, eq.inverter_specs, "Pac")
    except Exception:  # noqa: BLE001 - equipment prefill is best-effort
        pass
    return out


def default_module_label(wattage: int | None, index: int) -> str:
    """Pre-fill label: '615 W Module' from pysam, else 'Module N'."""
    if wattage:
        return f"{wattage} W Module"
    return f"Module {index + 1}"


def module_from_workbook(workbook_path: str | Path, label: str) -> ReportModule:
    """Parse one workbook into a ReportModule (year, 3-hr-avg Isc, Voc rows)."""
    runs, _warnings = parser.extract_runs(workbook_path)
    rows = [
        ResultRow(year=r.year, isc_3hr_avg=r.max_isc_rolling_avg_a, voc=r.max_voc_v)
        for r in sorted(runs, key=lambda x: x.year)
    ]
    return ReportModule(
        label=label,
        source_workbook=Path(workbook_path).name,
        rows=rows,
    )


def build_context(project: ReportProject, modules: list[ReportModule]) -> ReportContext:
    """Assemble the context, deriving the year range and run count from the data.

    num_runs is the count of DISTINCT simulation years across all modules, so the
    narrative ("{num_runs} simulations from {year_start} to {year_end}") stays
    internally consistent even when modules span different year ranges. For the
    common single-module / shared-range case this equals the module's row count.
    """
    years: list[int] = sorted({r.year for m in modules for r in m.rows})
    year_start = years[0] if years else None
    year_end = years[-1] if years else None
    num_runs = len(years)

    return ReportContext(
        project=project,
        modules=modules,
        year_start=year_start,
        year_end=year_end,
        num_runs=num_runs,
    )
