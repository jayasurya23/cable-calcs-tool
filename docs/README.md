# Cable Web — Documentation

| Document | For whom | Contents |
|---|---|---|
| [INTERN_GUIDE.md](INTERN_GUIDE.md) | Engineers & interns | Quick start: first report in 10 minutes, multi-module, troubleshooting |
| [USER_MANUAL.md](USER_MANUAL.md) | Engineers using the tool | How to sign in and run the full Project → Analysis → Report → Revision workflow, prepare SAM inputs, read the report, and troubleshoot. |
| [HANDOFF.md](HANDOFF.md) | Whoever maintains/operates it | System overview, Azure resource inventory, build & deploy, user/secret administration, data & backups, monitoring, report-template maintenance, an operations playbook, troubleshooting, security & cost, and known issues. |
| [SAM_REPORT.md](SAM_REPORT.md) | Developers / reviewers | The SAM module in depth: required input formats (workbook + pysam), the calculations, assumptions, limitations, and how to run the tests. |
| [../DEPLOY.md](../DEPLOY.md) | Whoever provisions Azure | First-time provisioning + deploy steps and cost. |
| [../README.md](../README.md) | Developers | Architecture, module layout, local run. |

**Secrets** (DB password, Entra client secret) are never in these docs or the
repo — they live in `deploy/config.env` (gitignored) and as Azure Container App
secrets. See HANDOFF.md §1.

Tests: `pip install -r ../requirements.txt -r ../requirements-dev.txt && python -m pytest`
(from `cable-web/`).
