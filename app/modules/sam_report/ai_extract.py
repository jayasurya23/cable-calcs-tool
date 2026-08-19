"""
Optional AI extraction of module identity from a datasheet.

WHY THIS EXISTS. Identification runs deterministically first — match the module
against the CEC database by name, then by its nameplate numbers. Those handle
almost everything and cost nothing. What they cannot handle is a datasheet whose
text is unusable: a scanned or image-only PDF (no text at all to read), or a
layout where the model designation never appears as recoverable text. This module
covers exactly that gap, using a vision-capable model so an image-only sheet
still works.

PROVIDERS. OpenAI and Anthropic are both supported; the provider is chosen by
whichever API key is configured (AI_PROVIDER forces one). Adding a provider means
adding one function below — the schema, the prompt and every safety rule are
shared, so the behaviour can't drift between them.

SAFETY PROPERTIES, deliberately:
  * OFF unless an API key is configured. With no key the app behaves exactly as
    it did before — nothing new can fail.
  * Runs ONLY after the deterministic paths have failed, so a client datasheet is
    sent off-network only for a genuinely unidentified module, never routinely.
  * The result is a SUGGESTION. It is surfaced for the engineer to confirm, never
    written silently into a report — the same rule the text-scraped path follows,
    because an engineering deliverable must not carry an unverified module name.
    Once confirmed, "Save to library" makes it permanent and no further AI call is
    ever made for that module.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)

# Datasheets are a few hundred KB; anything larger is not a datasheet.
_MAX_PDF_BYTES = 8 * 1024 * 1024


class ExtractedModule(BaseModel):
    """What a PV module datasheet states about the module it describes."""

    manufacturer: str = Field(description="Brand as printed, e.g. 'Qcells North America'. Empty string if not stated.")
    model: str = Field(description="Exact model/part designation, e.g. 'Q.PRO-G3 245'. Empty string if not stated.")
    pmax_w: float | None = Field(description="Nominal/maximum power at STC, in watts.")
    voc_v: float | None = Field(description="Open-circuit voltage at STC, in volts.")
    isc_a: float | None = Field(description="Short-circuit current at STC, in amps.")
    vmp_v: float | None = Field(description="Voltage at maximum power at STC, in volts.")
    imp_a: float | None = Field(description="Current at maximum power at STC, in amps.")
    cells: int | None = Field(description="Number of cells in series, if stated.")
    confidence: str = Field(description="'high' if read directly off the sheet; 'low' if inferred or unclear.")


_PROMPT = """This is a photovoltaic module (solar panel) datasheet.

Extract the module's identity and its STC nameplate electrical values.

Rules:
- Report the manufacturer and the exact model/part designation as printed. Do NOT
  return marketing copy ("High-Efficiency Bifacial Module"), a cell technology
  ("TOPCon 16BB"), a series name, or a document revision line as the model.
- If the sheet covers several wattage variants in one table, return the values for
  the HIGHEST wattage variant and give its full model designation.
- Use STC values (1000 W/m2, 25 C), not NOCT/NMOT.
- If a value genuinely isn't stated, return null rather than guessing.
- Set confidence to 'low' if the scan is poor or you had to infer the model."""


def provider() -> str:
    """Which provider to use: an explicit setting, else whichever key is set."""
    chosen = (getattr(settings, "ai_provider", "") or "").strip().lower()
    if chosen:
        return chosen
    if getattr(settings, "openai_api_key", ""):
        return "openai"
    if getattr(settings, "anthropic_api_key", ""):
        return "anthropic"
    return ""


def _api_key(name: str) -> str:
    return getattr(settings, f"{name}_api_key", "") or ""


def is_enabled() -> bool:
    """AI extraction is opt-in: it needs a provider key to be configured."""
    if not getattr(settings, "ai_datasheet_extraction", True):
        return False
    p = provider()
    return bool(p and _api_key(p))


def _default_model(p: str) -> str:
    configured = (getattr(settings, "ai_model", "") or "").strip()
    if configured:
        return configured
    return {"openai": "gpt-4o", "anthropic": "claude-opus-5"}.get(p, "")


def extract(pdf_path: str | Path) -> dict | None:
    """Read a datasheet with the configured model. Returns a dict shaped like the
    deterministic parser's output, or None if disabled or anything goes wrong —
    every failure falls back to existing behaviour rather than blocking an upload."""
    if not is_enabled():
        return None
    path = Path(pdf_path)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > _MAX_PDF_BYTES:
        return None

    p = provider()
    try:
        found = _OPENAI(raw, path.name) if p == "openai" else _ANTHROPIC(raw)
    except Exception as exc:  # noqa: BLE001 - never let this break an upload
        log.warning("AI datasheet extraction (%s) failed: %s", p, exc)
        return None
    if found is None:
        return None

    model = (found.model or "").strip()
    mfr = (found.manufacturer or "").strip()
    if not model and not mfr:
        return None

    specs = {}
    for key, value in (("pmax", found.pmax_w), ("voc", found.voc_v), ("isc", found.isc_a),
                       ("vmp", found.vmp_v), ("imp", found.imp_a)):
        if value is not None:
            specs[key] = float(value)

    return {
        "display": f"{mfr} — {model}" if mfr and model else (model or mfr),
        "manufacturer": mfr, "model": model, "specs": specs,
        "cells": found.cells, "confidence": found.confidence, "provider": p,
    }


def _OPENAI(raw: bytes, filename: str) -> ExtractedModule | None:
    """OpenAI Responses API. PDFs go in whole as an input_file (data URL), so a
    scanned sheet is handled by the model's vision rather than needing OCR here."""
    from openai import OpenAI

    client = OpenAI(api_key=_api_key("openai"))
    data_url = "data:application/pdf;base64," + base64.standard_b64encode(raw).decode("ascii")
    response = client.responses.parse(
        model=_default_model("openai"),
        input=[{
            "role": "user",
            "content": [
                {"type": "input_file", "filename": filename or "datasheet.pdf",
                 "file_data": data_url},
                {"type": "input_text", "text": _PROMPT},
            ],
        }],
        text_format=ExtractedModule,
    )
    return response.output_parsed


def _ANTHROPIC(raw: bytes) -> ExtractedModule | None:
    from anthropic import Anthropic

    client = Anthropic(api_key=_api_key("anthropic"))
    response = client.messages.parse(
        model=_default_model("anthropic"),
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": base64.standard_b64encode(raw).decode("ascii")}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
        output_format=ExtractedModule,
    )
    return response.parsed_output
