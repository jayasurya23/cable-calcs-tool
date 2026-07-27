"""Report assembly: pysam prefill, year/run derivation, and template filling
(every «TOKEN» substituted, Results table injected) for both report styles."""
from __future__ import annotations

import io
import zipfile

from app.modules.sam_report import docx_fill, report_builder
from app.modules.sam_report.report_models import ReportProject


def test_prefill_from_pysam(pysam_inputs):
    p = report_builder.prefill_from_pysam(pysam_inputs)

    assert p["coordinates"] == "33.4500, -112.0700"         # from solar_resource_file
    assert p["gcr"] == "0.36"
    assert p["modules_per_string"] == "26"
    assert p["system_size_dc"] == "2600.0 kW"
    assert p["dc_ac_ratio"] == "1.30"                       # 2600 DC / (125 kW * 16 = 2000 AC)
    assert p["module_wattage"] == 535
    assert p["weather_file"] == "phoenix_33.4500_-112.0700_nsrdb_60_2001.csv"
    assert p["albedo_text"] == "A uniform albedo of 0.25 was applied for all months."
    assert p["module_model"] == "CEC Performance Model — 535.15 W"


def test_build_context_derives_year_range_and_run_count(sam_workbook):
    module = report_builder.module_from_workbook(sam_workbook, "615 W Module")
    ctx = report_builder.build_context(ReportProject(), [module])

    assert (ctx.year_start, ctx.year_end, ctx.num_runs) == (2001, 2002, 2)
    assert ctx.year_range == "2001–2002"
    # rows are sorted by year; isc_3hr_avg carries the rolling-average value
    assert (module.rows[0].year, module.rows[0].isc_3hr_avg) == (2001, 14.0)
    assert module.rows[1].voc == 1441.93


def test_fill_docx_substitutes_every_token_for_both_templates(sam_workbook):
    module = report_builder.module_from_workbook(sam_workbook, "615 W Module")
    ctx = report_builder.build_context(
        ReportProject(project_name="Test Project", project_id="001", date="01/01/2026"),
        [module],
    )

    for template in ("classic", "modern"):
        data = docx_fill.fill_docx(ctx, template=template)
        z = zipfile.ZipFile(io.BytesIO(data))
        body = z.read("word/document.xml").decode("utf-8")
        header = z.read("word/header1.xml").decode("utf-8")

        leftover = docx_fill._TOKEN_RE.findall(body) + docx_fill._TOKEN_RE.findall(header)
        assert leftover == [], f"{template} template left unfilled tokens: {leftover}"

        # the Results table was injected with both module years present
        assert "2001" in body and "2002" in body
