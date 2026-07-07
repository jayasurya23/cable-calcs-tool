"""SAM analysis reports module.

Engineers run their SAM analysis, then upload:
  * a multi-run Excel workbook (all runs), and
  * (optionally) a pysam JSON case-inputs file.

This module parses those, runs a few calcs, and produces a report + a table
destined for CAD files. The exact run schema is finalized against a real sample
workbook (see ``parser.py``).
"""
