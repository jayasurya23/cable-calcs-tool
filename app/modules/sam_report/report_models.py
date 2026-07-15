"""
Data models for the SAM *report* (the Franklin-style deliverable).

A report is built from:
  * one or more module run-sets (each an uploaded SAM workbook -> per-year
    Max 3-hr-avg Isc + Max Voc), and
  * project details entered on the report form (cover sheet + header fields),
    some of which are pre-filled from the Excel (year range / run count) and the
    pysam JSON (coordinates / GCR / modules-per-string).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResultRow(BaseModel):
    """One year's worst-case values for a module (the Results table row)."""

    year: int
    isc_3hr_avg: float   # "Max of 3 Hr Avg Isc"
    voc: float           # "Max Voc"


class ReportModule(BaseModel):
    """One module column-block in the Results table."""

    label: str                         # e.g. "460 W Module" (engineer-entered)
    source_workbook: str               # staged filename this data came from
    rows: list[ResultRow] = Field(default_factory=list)

    @property
    def max_isc_3hr(self) -> float:
        return max((r.isc_3hr_avg for r in self.rows), default=0.0)

    @property
    def max_voc(self) -> float:
        return max((r.voc for r in self.rows), default=0.0)


class ReportProject(BaseModel):
    """Cover-sheet + header fields. Free-text so the engineer stays in control."""

    # Header / cover identity
    project_name: str = "Franklin Solar, LLC"
    project_id: str = ""
    date: str = ""                     # MM/DD/YYYY (defaults to today)
    revision: str = "0"
    # Report style: "classic" (Franklin) or "modern" (AmpCalc cover/letterhead).
    template: str = "classic"

    # Project Information table (pre-filled from pysam where possible)
    coordinates: str = ""
    gcr: str = ""
    modules_per_string: str = ""
    module_model: str = ""
    inverter_model: str = ""
    dc_ac_ratio: str = ""
    system_size_dc: str = ""

    # Inputs — albedo paragraph, derived from the pysam monthly albedo, editable.
    albedo_text: str = ""
    # Inputs — weather-data reference (NSRDB), from the pysam solar_resource_file
    # basename; shown on the "Weather Data:" line of the Inputs section.
    weather_file: str = ""

    # Cover sheet blocks
    owner_name: str = ""
    owner_phone: str = ""
    epc_name: str = ""
    epc_phone: str = ""
    eng_firm_name: str = "Castillo Engineering Services LLC"
    eng_firm_phone: str = "407-289-2575"
    eor: str = ""                      # Engineer of Record (name, PE#)
    designer: str = ""
    checker: str = ""                  # "Check:"


class ReportContext(BaseModel):
    """Everything the report template needs to render."""

    project: ReportProject
    modules: list[ReportModule] = Field(default_factory=list)

    # Auto-derived from the module data (the "update automatically" fields).
    year_start: int | None = None
    year_end: int | None = None
    num_runs: int = 0

    # TOC page numbers, filled by the two-pass PDF renderer (section slug -> page).
    toc_pages: dict[str, int] = Field(default_factory=dict)

    @property
    def year_range(self) -> str:
        if self.year_start and self.year_end:
            return f"{self.year_start}–{self.year_end}"
        return ""
