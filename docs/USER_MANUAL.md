# Cable Web — User Manual (SAM Analysis Reports)

A guide for engineers using the tool to turn SAM simulation output into the
standardized SAM Analysis report. No installation needed — it runs in your
browser.

**Site:** https://cable-web-castillo.nicesand-bffcb719.eastus2.azurecontainerapps.io

---

## 1. What the tool does

You upload the parametric-runs workbook from **SAM** (System Advisor Model). The
tool reads the per-year maximum open-circuit voltage (**Voc**) and short-circuit
current (**Isc**, plus its 3-hour rolling average), and produces:

- a standardized **Output workbook** (a copy of yours with an added "Output" tab), and
- a formatted **SAM Analysis report** as **PDF + editable Word**, using your
  team's cover/letterhead.

Everything is organized by **project** and kept under version control, so every
report you generate is saved and downloadable later.

## 2. Getting access & signing in

1. You need a **Castillo Microsoft 365 account** — the same login as Outlook/Teams.
   Access is limited to `@castillope.com` accounts.
2. Open the site and click **Sign in** → sign in with Microsoft. No separate
   password to remember.
3. First time you sign in, an account is created automatically. If you need
   admin rights (managing users), ask an existing admin
   (currently **jbhaskar@** or **mpuri@castillope.com**).

> First load after the site has been idle overnight/weekend can take ~10–40 s to
> wake up. During work hours (Mon–Fri, 7am–7pm ET) it's kept warm and loads fast.

## 3. The workflow

The tool is organized as **Project → Analysis → Revision**:

- A **Project** is one job/site. It stores the cover-sheet details and holds all
  its analyses.
- An **Analysis** is one saved SAM setup (a scenario) you can reopen and re-run.
- A **Revision** (R0, R1, R2 …) is one issued version of the report. The newest
  revision's PDF is the current deliverable.

### Step 1 — Create or open a project
- **Projects** → **+ Create project** (name + Project ID), or open an existing one.
- Expand **Project details & cover-sheet defaults** and fill Owner / EPC /
  Engineering firm / EOR / Designer / Checker once — these prefill every report
  in the project.

### Step 2 — Start an analysis
- On the project page, **+ New analysis**.
- **Name it** something you'll recognize (e.g. *"Boviet 615 W"*).
- Upload your **SAM runs workbook** (`.xlsx`). Optionally attach the **pysam
  inputs JSON** — it auto-fills coordinates, GCR, modules-per-string, equipment,
  and the albedo statement (see §4).
- Click **Analyze**.

### Step 3 — Review what was read
- Check the **Output table** (Year, Max Isc, Max Voc, 3-hr rolling avg, and the
  Maximum row). You can download the Output workbook here.
- If a pysam file was attached, review the **Equipment** section and type the real
  **module/inverter product names** if you want them on the report (SAM files
  usually don't carry product names).

### Step 4 — Fill the report form & generate
- Most fields are **pre-filled** (project cover sheet, year range, run count,
  Project Information, albedo). Adjust anything.
- Add extra module workbooks with **Add module** if the report should show several
  modules side by side.
- **Generate report** → a full **preview** appears → **Download PDF** or **Word**.

### Step 5 — Revisions
- Every **Generate** files the next revision (R0, R1, R2 …). On the project page,
  each analysis shows the **current deliverable** (download the PDF) and a
  collapsible **revision history** with every version, who filed it, when, and all
  the files.
- To issue a corrected version of an existing revision, use **Save as → Re-issue**
  on the form (documents are replaced, a re-issue is counted).
- **Edit & re-run** reopens an analysis with its last inputs so you can tweak and
  generate a new revision.

### Combine analyses
- With two or more analyses in a project, **Combine analyses…** builds one report
  whose Results table places them side by side. It's filed as the project's
  **Combined** analysis with its own revision history.

### Delete
- **Delete** on an analysis card removes that analysis and its revisions;
  **Delete project** (in Project details) removes the whole project. Both ask for
  confirmation and cannot be undone.

## 4. Preparing your inputs

- **SAM runs workbook (required):** In SAM, run your **parametric simulation**,
  then export the results to Excel. The tool needs the sheets for subarray
  **open-circuit voltage**, string **short-circuit current**, and (ideally) the
  **solar-resource library** (used to label each run's year). It matches sheet
  names loosely, so SAM's default export works as-is.
- **pysam inputs JSON (optional):** In SAM, use **Generate code → JSON for
  inputs**. It's only used to pre-fill the form — the report never depends on it.

The exact format, and what each input drives, is in
[`SAM_REPORT.md`](SAM_REPORT.md) (§2–3).

## 5. Understanding the report

- **Cover** — project name, SAM REPORT, Owner/EPC/Engineering blocks, prepared/
  checked by, date.
- **Table of Contents** — the 7 sections with page numbers.
- **Sections** — Description/Purpose, Method of Analysis, Inputs (module/inverter
  datasheets, weather, albedo), Project Information, **Results**, Conclusion,
  Appendix.
- **Results table** — one row per simulation year with **Max Voc (V)** and the
  **3-hr rolling-average Isc (A)** (the design current used for DC wiring per
  NEC 690.8(A)(1)(2)), plus a highlighted **Maximum** row.

## 6. Tips & troubleshooting

| Situation | What to do |
|---|---|
| Site is slow to load | It was asleep (off-hours). Wait a few seconds for the first load; it's warm during work hours. |
| "This doesn't look like a SAM export" warning | The workbook is missing the open-circuit / short-circuit sheets. Re-export the parametric results from SAM. |
| Equipment shows a model type, not a product name | SAM files rarely include product names — type the real module/inverter name in the Equipment section. |
| An old analysis won't reopen | It will rebuild its working files from its latest saved report automatically; if there's no saved report to rebuild from, download what's filed and start a new analysis. |
| I need admin rights / a user removed | Ask an admin (jbhaskar@ / mpuri@castillope.com); admins manage accounts under **Users**. |
| The report cover/TOC looks off | Tell the maintainer — report rendering can differ slightly by server version; see the handoff doc. |

Questions or issues → contact the tool's maintainer (see
[`HANDOFF.md`](HANDOFF.md)).
