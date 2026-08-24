"""MySQL DBA catalog compatibility wrapper."""

from pathlib import Path

from evals.dba.catalog import AnswerCheck, EvalCase, EvalCatalog, EvidenceRequirement
from evals.dba.catalog import load_catalog as _load_catalog

SUITE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG_PATH = SUITE_DIR / "cases.json"


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> EvalCatalog:
    """Load the versioned MySQL DBA case catalog."""
    return _load_catalog(path)


__all__ = ["AnswerCheck", "EvalCase", "EvalCatalog", "EvidenceRequirement", "load_catalog"]
