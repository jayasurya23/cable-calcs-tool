# Cable Web

Web version of the PV cable tooling, hosted on Azure. This is a **single FastAPI
app** that grows module-by-module. First module: **SAM Analysis Reports**. The
full DC/AC/MV cable-calc suite lands here later as another module.

Built to match the conventions of the sibling `sam-weather-service` (FastAPI +
`pydantic-settings` + `.env`, pinned `requirements.txt`, `/health` probe).

## Architecture

```
cable-web/
├── app/
│   ├── main.py                 FastAPI app: CORS, static, /health, /, router mounts
│   ├── config.py               pydantic-settings (env / .env)
│   ├── core/templating.py      Jinja2 + static paths
│   ├── shared/engine.py        bridge to the desktop calc engines (single source of truth)
│   ├── modules/
│   │   └── sam_report/         SAM upload → parse → Output sheet + PDF report
│   │       ├── router.py       routes (upload, equipment, report form/generate/pdf)
│   │       ├── service.py      upload orchestration (stage → parse → Output workbook)
│   │       ├── parser.py       xlsx + pysam JSON parsing
│   │       ├── models.py       upload-side models (SamRun, SamReport, Equipment)
│   │       ├── excel_writer.py standardized "Output" sheet writer
│   │       ├── report_models.py   ReportProject / ReportModule / ReportContext
│   │       ├── report_builder.py  auto-fill from Excel (years/runs) + pysam (coords/GCR/mps)
│   │       ├── report_service.py  module mgmt + generate_report (docx + pdf)
│   │       ├── docx_fill.py       fill the Word template + generate the Results table
│   │       ├── converter.py       DOCX→PDF (LibreOffice / Word COM)
│   │       └── assets/            report_template.docx (+ build_template.py)
│   ├── templates/              base.html, index.html, sam_report/*
│   └── static/                 css / js
├── requirements.txt
├── startup.sh                  Azure App Service startup command
└── run_dev.ps1                 local dev launcher
```

**Adding a module later** (e.g. `cable_calcs`): create `app/modules/<name>/` with
its own `router.py`, add `app.include_router(...)` in `main.py`, drop templates
under `app/templates/<name>/`. Nothing else is global.

### Shared calc engines

The NEC math is **not duplicated**. `app/shared/engine.py` puts the desktop
app's engine directory (`../Cable-Optimisation-app`) on `sys.path` and imports
`nec_tables`, `ac_calculation_engine`, `calculation_engine`. `calculation_engine`
was made headless-safe (its tkinter import is now optional) so it loads on a
server with no Tk. `GET /health` reports `engines_available`.

Override the engine location with `ENGINE_DIR` (env) when deploying.

## Users, projects & version control

The app is a **standalone multi-user tool** (SQLite + SQLAlchemy; swap
`DATABASE_URL` for Azure SQL/Postgres):

- **Sign-in**: `AUTH_MODE=entra` uses Microsoft Entra ID (M365) via MSAL —
  users are provisioned on first sign-in, `ADMIN_EMAILS` become admins.
  `AUTH_MODE=local` (default) is the standalone fallback: first run opens
  `/setup` to create the admin, who adds engineers on `/admin/users`.
  Sessions are DB-backed httpOnly cookies (only the token hash is stored).
- **Roles**: `admin` (user management + everything) and `engineer`.
- **Projects** (`/projects`): each project stores the Project ID and the
  cover-sheet defaults (Owner/EPC/Engineering firm/EOR/Designer/Check) that
  prefill every report form. SAM analyses are launched from the project page.
- **Version control**: every generated report is filed under
  `data/projects/<id>/rev<N>/` with the **full document set** — SAM Report
  .docx + .pdf, the standardized Output workbook, the source runs Excel,
  the pysam JSON, extra module workbooks, and a `form.json` snapshot of the
  form inputs. "Save as" on the report form picks **New revision (R{n})**
  (default) or **re-issue** of an existing revision (documents replaced,
  re-issue counted). The project page lists every revision with who/when/label
  and per-document download links. All actions land in an `audit_events` table.

## Run locally

```powershell
./run_dev.ps1
```
Then open http://127.0.0.1:8000  ·  API docs at `/docs`  ·  health at `/health`.

Manual equivalent:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Deploy to Azure (App Service, Linux)

1. Create a Linux **Python 3.11+** App Service.
2. Deploy this folder (zip deploy / GitHub Actions).
3. **Configuration → General settings → Startup Command:** `bash startup.sh`
4. **Configuration → Application settings:** set `ENGINE_DIR` if the desktop
   engine modules are deployed to a non-default path (or deploy them alongside).
5. Health check path: `/health`.

### DOCX → PDF (report rendering)

The report PDF is produced by converting the filled Word template with
**LibreOffice** (`soffice`, headless) on Azure, falling back to **Word COM** on a
local Windows box that has Word but not LibreOffice (`converter.py`). The Docker
image installs `libreoffice-writer` + the Jost/Carlito fonts; nothing else is
needed at runtime. **Note:** LibreOffice renders the template differently from
Word (and between LO versions) — verify template changes with a LibreOffice
render, not Word. See [`docs/SAM_REPORT.md`](docs/SAM_REPORT.md) §5.

## Documentation & tests

- **[`docs/SAM_REPORT.md`](docs/SAM_REPORT.md)** — usage, required input formats
  (SAM workbook + pysam JSON), the calculations, assumptions, limitations, and
  how to run the tests.
- **`tests/`** (pytest) — the SAM calculations, parser robustness, and report
  fill, checked against hand-verified values:
  ```bash
  pip install -r requirements.txt -r requirements-dev.txt
  python -m pytest
  ```

## SAM report — current status

- ✅ Upload runs `.xlsx` (+ optional pysam `.json`), staged under `uploads/`.
- ✅ Run extraction: finds the "Subarray 1 Open circuit DC volt" /
  "Subarray 1 String short circuit" sheets (fuzzy name match), one column per
  run; run years come from the solar-resource-library weather-file names
  (fallback: Run 1 = 1998, consecutive).
- ✅ Per-year stats: **Max SAM Isc (A)**, **Max SAM Voc (V)**, and
  **Max SAM Isc Rolling Average (A)** — max of the 3-hr trailing rolling
  average of string Isc (window auto-scales for sub-hourly data).
- ✅ Standardized **Output sheet** written into a downloadable copy of the
  uploaded workbook (`GET /sam/download/...`): table anchored at B4,
  theme-3/75%-tint header + Maximum rows, thin grid, centered, `0.00` formats,
  sample-matched column widths. Verified value-for-value (full float
  precision) against the reference "SAM excel output.xlsx".
- ✅ Non-SAM workbooks degrade gracefully to a raw sheet preview + warning.
- ✅ Optional **pysam JSON** (pinned against a real `pvsamv1` export): a
  pvsamv1 inputs JSON has no product-name strings, so `extract_equipment()`
  reads the `module_model` / `inverter_model` selectors → SAM model-type label,
  and pulls nameplate specs from the active parameter family
  (`cec_*` → Pmax/Vmp/Imp/Voc/Isc/cells; `inv_snl_*`/`inv_ds_*`/`inv_pd_*`/
  `inv_cec_cg_*` → Pac/Pdc/Vdc-max/MPPT range/qty). Product name is still shown
  if a name-bearing export ever provides one. Rendered in an Equipment section.
- ✅ **Manual product names**: since pysam has no name strings, the Equipment
  section has editable Module/Inverter name inputs. `POST /sam/equipment/{id}`
  persists them to `equipment_overrides.json` in the upload's staging dir
  (path-traversal-guarded), for the later report-generation step to read
  alongside the SAM-derived specs.

## Report (Franklin-style deliverable — editable Word + PDF)

"Generate report" opens a form, then a full preview, then downloadable **Word
(.docx) + PDF**. The report is produced by filling the **actual Word template**
the engineer's team uses (`assets/report_template.docx`), so it is byte-for-byte
the real deliverable — not a re-creation. Everything (report + website) is in the
**Jost** typeface.

- **Template** (`assets/report_template.docx`): the reference report with the
  Franklin-specific values replaced by `«TOKEN»` placeholders and the Results
  table replaced by a `«RESULTS_TABLE»` marker; fonts switched to Jost with
  size/autofit tweaks that compensate for Jost's larger metrics (so the
  Arial-tuned header/cover don't clip). Regenerate it with
  `assets/build_template.py` if the source template changes.
- **Form** (`GET /sam/report/{id}/form`): project/cover/header fields, prompted
  where needed. Auto-filled — **year range + run count** from the Excel;
  Project Information (**Coordinates / GCR / Modules-per-string / Module model /
  Inverter model / DC-AC ratio / System size**) and the **Inputs albedo
  statement** from pysam (`report_builder.prefill_from_pysam`: DC/AC =
  system_capacity ÷ total inverter AC; albedo prose derived from the monthly
  `albedo` array — "uniform 0.20" or a snow/non-snow month split; module/inverter
  = manual name if entered on the upload page, else SAM model type + nameplate).
  All editable. One Excel per module; "Add module" stages extra workbooks
  (`POST …/module`, `…/module/remove/{i}`), state in `modules.json`.
- **Generate** (`POST …/generate`): `docx_fill.fill_docx()` substitutes tokens
  in `word/document.xml` + `word/header1.xml` (values XML-escaped) and injects a
  generated Results table (`#00B0F0` module headers, `#FFFF00` sub/Max rows,
  N modules side-by-side). `converter.docx_to_pdf()` then renders the PDF via
  **LibreOffice** (`soffice`, on Azure) or **Word COM** (local-dev fallback) —
  which also updates the Word-native TOC page numbers and PAGE fields. Both files
  are written atomically.
- **Preview + download**: `GET …/document.pdf` (inline in an iframe) and
  `GET …/document.docx` (editable Word). The preview is the exact PDF.
