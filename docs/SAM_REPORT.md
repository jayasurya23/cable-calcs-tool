# SAM Analysis Report — usage, inputs, assumptions & testing

The SAM module turns the parametric-runs export from **SAM (System Advisor
Model)** into a standardized per-year table (Max Isc, Max Voc, 3-hr rolling-average
Isc) and a formatted **Word + PDF report** used as the engineering deliverable.

This document covers what it expects as input, what it computes, the assumptions
and limitations baked in, and how to run the tests. For architecture and
deployment see [`README.md`](../README.md) and [`DEPLOY.md`](../DEPLOY.md).

---

## 1. Usage

Sign in with your Microsoft account (Entra) — or a local account in standalone
mode — then:

1. **Projects** → open or create a project. A project holds the cover-sheet
   defaults (Owner / EPC / Engineering firm / EOR / Designer / Checker) that
   prefill every report, plus the Project ID.
2. **+ New analysis** → name it (e.g. *"Boviet 615 W"*), then upload the SAM
   **runs workbook** (`.xlsx`) and, optionally, the **pysam inputs JSON**.
3. Review the auto-parsed **Output table** and equipment, fill/adjust the report
   form (most fields are pre-filled), then **Generate report**.
4. Each generate is filed as the next **revision** (R0, R1, R2 …). The newest
   revision's PDF is the current deliverable; the whole document set (PDF, Word,
   Output workbook, source files, form snapshot) is kept per revision.
5. **Combine analyses** produces one report whose Results table places several
   analyses side by side. **Delete** removes an analysis or a whole project
   (DB rows + files), with a confirmation.

An analysis is a *saved scenario* — reopen it any time to edit inputs and re-run;
if its working files were ever cleared, re-open rebuilds them from the latest
filed revision.

---

## 2. Required input formats

### 2.1 SAM runs workbook (`.xlsx`, required)

Produced by SAM's **parametric simulation → export to Excel**. The parser looks
for three sheets by **fuzzy keyword match** on the (lower-cased) sheet title, so
Excel's 31-character title truncation and minor SAM suffix changes are tolerated:

| Purpose | Keyword(s) matched | Expected layout |
|---|---|---|
| Subarray open-circuit voltage | `open circuit` | Row 1 = `Hour, Run 1, Run 2, …`; then one row per timestep of hourly **Voc (V)** |
| String short-circuit current | `short circuit` | Same shape; hourly **Isc (A)** |
| Weather / resource per run | `solar resource` | Row 1 = `Hour, Run 1, …`; row 2 = the **weather-file name** for each run |

- Column **A** is the timestep index; each subsequent column is one parametric
  **run**. Run columns are matched by their header label between the Isc and Voc
  sheets.
- **8 760 hourly rows** is the normal case. Sub-hourly data is supported — the
  rolling-average window auto-scales (see §3).
- The **weather sheet is optional**. When present, the run year is read from the
  weather-file name (the last `19xx`/`20xx` in it, e.g. `…_60_2001` → 2001).
- Cell values may be **numbers or numeric text** (SAM sometimes writes numbers as
  strings) — both are accepted. A workbook missing the two value sheets is **not
  rejected**: it degrades to a raw sheet preview plus a warning.

### 2.2 pysam inputs JSON (optional)

A `pvsamv1` case-inputs file (SAM → *Generate code → JSON for inputs*). It is used
only to **pre-fill** the report form — never required. It is a flat map where
every value is a string, equipment is chosen by numeric selector, and there is
usually **no product-name string**. Extracted values:

- **Coordinates** from `solar_resource_file` (needs ≥3 decimal places).
- **GCR** (`subarray1_gcr`), **modules per string** (`subarray1_modules_per_string`).
- **System size (DC)** from `system_capacity`; **DC/AC ratio** = system_capacity ÷
  (active-inverter `*_paco` × `inverter_count`).
- **Module / inverter**: the `module_model` / `inverter_model` selector → a SAM
  model-type label, plus nameplate specs from the active parameter family
  (`cec_*`, `snl_*`, `sd11par_*`, `mlm_*` for modules; `inv_snl_*`, `inv_ds_*`,
  `inv_pd_*`, `inv_cec_cg_*` for inverters). A real product name is used instead
  if the export carries one.
- **Albedo statement** for the Inputs section (a uniform value, a month-by-month
  split, or "taken from the weather file" when `use_wf_albedo = 1`).

You can always type the real module/inverter product names on the form — those
override the pysam-derived labels.

---

## 3. What the tool computes

For each run the parser produces (`parser.extract_runs`):

- **Max SAM Isc (A)** — the maximum hourly string Isc.
- **Max SAM Voc (V)** — the maximum hourly subarray Voc.
- **3-hr rolling average (A)** — the maximum of the **trailing rolling average**
  of Isc over a 3-hour window. The window shrinks at the start of the series
  (`avg over min(t+1, window)`), and its size in samples is
  `3 × steps_per_hour`, where `steps_per_hour = max(1, round(rows / 8760))`
  (so hourly → 3 samples, 30-min → 6, etc.).
- **Year** — from the weather-file name if available, else `1998 + run_index`.

The **Output sheet** (written into a downloadable copy of the workbook, anchored
at cell B4) and the web table have columns
`Year | Max SAM Isc (A) | Max SAM Voc (V) | 3hr Rolling average`, one row per run,
plus a **"Maximum"** footer row that is the **column-wise max across all runs**.
The Output sheet keeps full float precision; the on-screen/report table shows
`0.00` (2-dp). The report's Results table uses the **3-hr rolling average** as the
design Isc and the **Max Voc** per year, with the plant-wide Maximum highlighted.

---

## 4. Assumptions

- **Sheet identity** is inferred from keywords, not exact titles. If SAM renames
  sheets so `open circuit` / `short circuit` / `solar resource` no longer appear,
  the run is treated as a non-SAM workbook (preview + warning).
- **Run 1 = 1998, consecutive years** whenever the weather sheet is absent or a
  weather-file name has no parseable year.
- **Runs align by header label** between the Isc and Voc sheets. If the two
  sheets disagree on the run set, the short-circuit (Isc) sheet's runs win and a
  warning is emitted.
- **Non-finite / non-numeric cells → 0.0.** Blank, `nan`, `inf`, and junk never
  reach the Isc/Voc numbers that feed NEC sizing.
- **The report body is fixed** at seven sections (Description, Method, Inputs,
  Project Information, Results, Conclusion, Appendix). The narrative line
  "*N simulations from Y₀ to Y₁*" uses the count of **distinct** years across all
  modules.
- **pysam is advisory only** — it pre-fills the form; every field is editable and
  the report never fails if pysam is missing or unparseable.

## 5. Limitations & known quirks

- **PDF fidelity depends on the LibreOffice version.** Azure renders with
  LibreOffice (headless), which differs from Microsoft Word and even between LO
  versions. Always verify template changes with a LibreOffice render of the same
  version family Azure runs, not with Word. (History: a cover PE-seal image
  rendered only on LO 25.2 and was invisible in Word/LO 26 — see the modern
  template build notes.)
- **The modern-template Table of Contents uses a baked static result.**
  LibreOffice does not refresh a Word content index on headless convert, so the
  TOC entries + page numbers are pre-computed for the fixed 7-section layout.
  Word refreshes them live on open; on the server they are correct for the
  standard report geometry but would not follow an unusually long Results table.
- **Year detection is filename-based.** A weather file with no `19xx/20xx`, or
  with a stray year-like number, can mis-assign the year — falls back to
  `1998 + index`.
- **Equipment prefill is best-effort** and only covers the pvsamv1 model families
  listed in §2.2; other SAM models fall through to blank fields.
- **The Output sheet's header fill uses the workbook's own theme** (theme-3 @ 75%
  tint) so it matches SAM's styling — it will look slightly different if a
  workbook ships a non-standard theme.

---

## 6. Testing

Tests live in [`tests/`](../tests) and use **pytest**. They build small,
format-correct fixtures (a 2-run SAM workbook and a pvsamv1-style pysam dict) with
values chosen so every expected output is worked out by hand — the reference
arithmetic is documented in [`tests/conftest.py`](../tests/conftest.py).

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

| File | Covers |
|---|---|
| `test_sam_calculations.py` | The core numbers vs. **hand-verified** values: 3-hr rolling-average max (incl. shrinking window), Max Isc/Voc per run, the "Maximum" footer, year assignment, and the Output-sheet layout/values. |
| `test_sam_parser.py` | Value coercion (text/`nan`/`inf`/blank → `0.0`), fuzzy sheet matching, weather-file year logic, graceful degradation of a non-SAM workbook, the 1998-fallback, and pysam equipment extraction. |
| `test_sam_report.py` | pysam → form prefill (coordinates, GCR, DC/AC, module label, albedo), year-range / run-count derivation, and full template fill for **both** the classic and modern styles (asserts **no `«TOKEN»` is left unsubstituted** and the Results table is injected). |

To validate against the **real reference export** rather than the synthetic
fixture, drop `SAM excel output.xlsx` beside the repo and compare
`parser.extract_runs(...)` / the generated Output sheet to the workbook's own
"Output" tab (verified value-for-value at full float precision during
development).
