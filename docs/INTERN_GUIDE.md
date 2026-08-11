# Cable Web — Quick Start Guide

**For engineers and interns producing SAM Analysis reports.**
No installation. It runs in your browser — sign in with your Castillo account.

---

## 1. What this tool does

You give it the **parametric-runs workbook exported from SAM**. It works out the
worst-case **maximum open-circuit voltage (Voc)** and **maximum short-circuit
current (Isc)** across every simulated year, and produces the standard Castillo
**SAM Analysis report** as a PDF and an editable Word file.

The Isc figure it reports is the **maximum of the 3-hour rolling average**, which
is what NEC 690.8(A)(1)(2) allows you to use for DC conductor sizing — that is the
whole point of running SAM instead of the 1.25 × Isc rule of thumb.

> **You are still the engineer.** The tool assembles the document; it does not
> check your design. Review every number before anything is issued or sealed.

---

## 2. Three words you need

| Term | What it means |
|---|---|
| **Project** | One job. Holds the cover-sheet details (Owner, EPC, EOR…) so you set them once. |
| **Analysis** | One SAM setup inside a project. You can reopen and re-run it any time. |
| **Revision** | Each time you generate a report it is filed as **R0, R1, R2…** Nothing is ever overwritten — the newest revision is the current deliverable. |

A project can hold several analyses (e.g. different module options), and each
analysis keeps its own revision history.

---

## 3. What to have ready

**Required — the SAM runs workbook (`.xlsx`)**
Exported from SAM after a parametric run. It must contain the per-year sheets
whose names include *"Open circuit DC volt…"* and *"String short circuit…"*.
If those sheets are missing, the tool will tell you it isn't a SAM runs export.

**Strongly recommended — one of these:**

| File | What it fills in |
|---|---|
| **pysam JSON** (SAM → *Generate code* → *JSON for inputs*) | Coordinates, GCR, modules-per-string, system size, DC/AC ratio, weather file, albedo — **and** names the module |
| **Module datasheet PDF** | Names the module and reads its nameplate specs — but **not** the system-design values above |

Without either, you can still produce a report — you just type those fields
yourself.

---

## 4. Your first report

**Step 1 — Open or create the project**
*Projects* in the top bar. Search by name or project ID. Create one with
**+ New project** if it doesn't exist yet.

**Step 2 — Fill in the project's cover-sheet defaults (once per job)**
On the project page, expand **Project details & cover-sheet defaults** and set
Owner, EPC, Engineering firm, EOR, Designer, Check. Every report in this project
will pre-fill from these — you won't retype them.

**Step 3 — Start a new analysis**
**+ New analysis**. Give it a name you'll recognise later (usually the module,
e.g. *"Boviet 615 W bifacial"*), then add your workbook and its pysam or
datasheet. Press **Analyze**.

Parsing takes roughly 15 seconds per module — that's normal.

**Step 4 — Check the numbers (tab 1, Results)**
You land on **Results**: the per-year table of Max Isc, Max Voc and the 3-hour
rolling average, with the worst case in the **Maximum** row. Sanity-check it
before going further. There is a **Workbook data** tab if you want to see the raw
sheets.

**Step 5 — Fill in the report details (tab 2)**
Most of it is already filled from your pysam. Check:

- **Project name / ID / Date** — appear in the page header
- **Project information** — coordinates, GCR, modules per string, module and
  inverter model, DC/AC ratio, system size
- **Inputs** — weather file and the albedo statement
- **Cover sheet** — from the project defaults; fill anything blank

Your typing is **auto-saved as a draft**, so you won't lose it if you navigate
away. You'll see *"Draft saved"* next to the Generate button.

**Step 6 — Generate**
Press **Generate report** in the bar at the bottom. It files the next revision
(the bar tells you which) and switches you to **Preview & download**, where you
can read the PDF and download the PDF or the Word file.

That's it. The documents are stored under the project — anyone on the team can
download them later.

---

## 5. Comparing several modules in one report

Add every module on the **New analysis** page: press **+ Add another module** and
give each its own workbook plus its own pysam or datasheet.

They appear **side by side** in the report's Results table, and the Project
Information block lists each module by name.

> Each module needs **its own pysam** for its System Size and DC/AC ratio to be
> filled in. A module with no pysam shows a dash for those — that's expected, not
> a bug.

---

## 6. About module names

The tool identifies the module for you and writes it as
**Manufacturer — Model** (e.g. *Qcells North America — Q.PRO-G3 245*).

It matches against the **CEC module database**, which updates itself weekly from
NREL. Two things worth knowing:

- A **green "pysam" or "datasheet" chip** on the module row means it was matched
  in the CEC database — that name is authoritative.
- An **amber "datasheet · check name" chip** means the name was read off the
  datasheet's text and is a **best guess — read it and correct it**. The specs
  are reliable; the name may not be.

If a module isn't in the CEC database at all, just type the correct name in the
**Module model** field. Ask an admin to add it to the *Module library* and it will
be recognised automatically from then on.

---

## 7. Revisions — new vs re-issue

At the bottom of the form:

- **New revision (R*n*)** — the normal choice. Files the next number and keeps
  everything before it.
- **Re-issue R*n*** — replaces the documents inside an existing revision. Use it
  only to correct a report you have just issued and not yet sent.

If a colleague files a revision while you have the form open, the tool will stop
you and ask you to reload rather than silently overwrite their work. Your typed
details are kept as a draft.

---

## 8. Attaching datasheets to the report

In the **Datasheets** section, drop in any module or inverter datasheet PDFs.
They are appended to the **end of the generated report PDF**, in the order you
add them.

Note this affects the **PDF only** — the editable Word file is the report itself,
because a PDF cannot be embedded into a Word document.

---

## 9. When something goes wrong

| What you see | What it means / what to do |
|---|---|
| *"That file isn't a readable .xlsx workbook"* | It's a PDF or CSV renamed to .xlsx, or a partial download. Re-export from SAM. |
| *"doesn't look like a SAM runs export"* | The workbook is missing the open-circuit / short-circuit sheets. Re-run the parametric export. |
| *"That file is larger than the 50 MB limit"* | Trim the workbook, or check you picked the right file. |
| *"This analysis changed while you were editing"* | Someone else filed a revision. Reload the page and generate again. |
| Report shows a dash for System Size / DC-AC | That module has no pysam. Expected — add one, or type the value. |
| Amber *"check name"* chip | The module name is a guess from the datasheet text. Verify it. |
| Nothing happens after Analyze | Give it ~15 s per module. If still nothing, reload and retry, then tell an admin. |

---

## 10. Habits worth having

1. **Generate early.** The first generate files R0 and proves the whole chain
   works. Refining after that is cheap — every version is kept.
2. **Name analyses properly.** *"Boviet 615 W bifacial"* beats *"test2"* when
   someone opens the project in six months.
3. **Use the revision label.** *"Issued for review"*, *"IFC"* — it shows in the
   history and saves guesswork later.
4. **One person per analysis at a time.** Two people editing the same one can
   overwrite each other's uploaded modules.
5. **Read the PDF before it goes out.** Especially the cover sheet, the module
   names, and the Maximum row.

---

## 11. Getting help

- **Help** in the top bar has the full user manual.
- Something broken, or a module missing from the library — ask an admin
  (currently Jayasurya Bhaskar and Manjil Puri).

---

*Cable Web — Castillo Engineering. Report content and engineering judgement remain
the responsibility of the Engineer of Record.*
