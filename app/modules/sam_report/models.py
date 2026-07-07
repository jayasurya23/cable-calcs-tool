"""Pydantic models for the SAM report module.

Schema pinned against the real sample workbook ("SAM excel output.xlsx"):
  * "Subarray 1 Open circuit DC volt"  — Hour + one column per run (hourly Voc)
  * "Subarray 1 String short circuit"  — Hour + one column per run (hourly string Isc)
  * "IN_Solar resource library curre…" — weather file per run (name ends in _YYYY)
  * Runs are consecutive weather years; Run 1 = 1998 unless the weather files say otherwise.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SheetPreview(BaseModel):
    """Lightweight description of one worksheet in an uploaded workbook."""

    name: str
    n_rows: int
    n_cols: int
    header: list[str] = Field(default_factory=list)
    sample_rows: list[list[str]] = Field(default_factory=list)


class SamRun(BaseModel):
    """One SAM analysis run (one weather year)."""

    run_id: str                       # e.g. "Run 1"
    year: int                         # weather year (Run 1 = 1998, ...)
    weather_file: str | None = None   # from the solar resource library sheet
    max_isc_a: float                  # max hourly string short-circuit current
    max_voc_v: float                  # max hourly subarray open-circuit voltage
    max_isc_rolling_avg_a: float      # max of the 3-hr rolling average of string Isc


class Equipment(BaseModel):
    """Equipment identifiers pulled from the optional pysam JSON.

    A pvsamv1 inputs JSON usually has NO product-name strings — the module and
    inverter are defined by numeric parameters. So we surface the SAM *model
    type* (from the module_model / inverter_model selectors) plus the defining
    nameplate specs. `module_model` / `inverter_model` hold a friendly product
    name only when the JSON actually carries one (e.g. a library-based export).
    """

    # Friendly product name if present in the JSON (often None for pvsamv1).
    module_model: str | None = None
    inverter_model: str | None = None
    # SAM model type from the *_model selector (e.g. "CEC (user-entered specs)").
    module_model_type: str | None = None
    inverter_model_type: str | None = None
    # Defining nameplate specs pulled from the active parameter family.
    module_specs: dict[str, str] = Field(default_factory=dict)
    inverter_specs: dict[str, str] = Field(default_factory=dict)
    # Any other equipment-name-ish strings found (leaf key -> value).
    extras: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(
            self.module_model or self.inverter_model
            or self.module_model_type or self.inverter_model_type
            or self.module_specs or self.inverter_specs or self.extras
        )


class ReportTableRow(BaseModel):
    """One row of the table that ultimately lands in the CAD files."""

    cells: dict[str, str | float | int | None] = Field(default_factory=dict)


class SamReport(BaseModel):
    """Result of parsing an upload + running the report calcs."""

    upload_id: str
    source_workbook: str
    source_pysam: str | None = None
    engines_available: bool = False

    sheets: list[SheetPreview] = Field(default_factory=list)
    runs: list[SamRun] = Field(default_factory=list)

    # Populated only when a pysam JSON is uploaded and parsed.
    equipment: Equipment | None = None

    # The standardized Output table (also written into the downloadable workbook).
    table_columns: list[str] = Field(default_factory=list)
    table_rows: list[ReportTableRow] = Field(default_factory=list)

    # Filename (within this upload's staging dir) of the workbook containing
    # the generated Output sheet; served by GET /sam/download/....
    output_filename: str | None = None

    warnings: list[str] = Field(default_factory=list)
