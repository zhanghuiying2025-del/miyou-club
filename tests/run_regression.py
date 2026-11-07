"""Run all tests against temporary stores, including Streamlit visit preferences."""
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from src import storage, performance, company_store
    with tempfile.TemporaryDirectory(prefix="opc-regression-") as directory, ExitStack() as stack:
        base = Path(directory)
        stack.enter_context(patch.object(storage, "DATA_DIR", base))
        stack.enter_context(patch.object(storage, "CASE_FILE", base / "current_case.json"))
        stack.enter_context(patch.object(performance, "PERFORMANCE_FILE", base / "performance.json"))
        stack.enter_context(patch.object(company_store, "COMPANY_DB", base / "company.sqlite3"))
        stack.enter_context(patch.dict(os.environ, {"HISTORY_REDIS_URL": "", "HISTORY_EMBEDDING_MODEL_PATH": ""}))
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
