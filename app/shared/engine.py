"""
Bridge to the desktop app's shared calculation engines.

The NEC math lives in the desktop app (``Cable-Optimisation-app/``) as a set of
flat modules — ``nec_tables``, ``ac_calculation_engine``, ``calculation_engine`` —
that import each other by bare module name. Rather than fork that logic (and risk
two diverging copies of the NEC tables), we put the engine directory on ``sys.path``
and import the pure-logic classes directly. This keeps a single source of truth.

``calculation_engine`` was made headless-safe (tkinter import is optional), so this
import works on an Azure server with no Tk installed.

Import from here, e.g.::

    from app.shared.engine import ACCalculationEngine, ENGINES_AVAILABLE
"""
import sys
from pathlib import Path

from app.config import settings

# Public flags/handles set below.
ENGINES_AVAILABLE = False
ENGINE_IMPORT_ERROR: Exception | None = None

# Names re-exported when the engines load. Declared up-front so linters/importers
# see stable symbols even if the import fails at runtime.
ACCalculationEngine = None
CalculationEngine = None
FileImporter = None
CECDatabase = None
nec_tables = None


def _register_engine_path() -> None:
    engine_dir = Path(settings.engine_dir)
    if engine_dir.is_dir():
        resolved = str(engine_dir.resolve())
        if resolved not in sys.path:
            sys.path.insert(0, resolved)


def _load_engines() -> None:
    global ENGINES_AVAILABLE, ENGINE_IMPORT_ERROR
    global ACCalculationEngine, CalculationEngine, FileImporter, CECDatabase, nec_tables
    try:
        import nec_tables as _nec_tables
        from ac_calculation_engine import ACCalculationEngine as _AC
        from calculation_engine import (
            CalculationEngine as _Calc,
            FileImporter as _FileImporter,
            CECDatabase as _CECDatabase,
        )
    except Exception as exc:  # noqa: BLE001 - surface any import failure to callers
        ENGINE_IMPORT_ERROR = exc
        ENGINES_AVAILABLE = False
        return

    nec_tables = _nec_tables
    ACCalculationEngine = _AC
    CalculationEngine = _Calc
    FileImporter = _FileImporter
    CECDatabase = _CECDatabase
    ENGINES_AVAILABLE = True


_register_engine_path()
_load_engines()
