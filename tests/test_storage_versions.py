import tempfile
import unittest
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from src.models import TravelCase
from src.storage import CaseConflictError, save_case_to_disk, load_case_from_disk, delete_case_from_disk


class StorageVersionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        for name, value in (("DATA_DIR", self.base), ("CASE_FILE", self.base / "current_case.json")):
            patcher = patch(f"src.storage.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_new_save_versions_and_stale_save_cannot_overwrite(self):
        case = TravelCase(customer_request="测试")
        save_case_to_disk(case)
        stale = load_case_from_disk()
        case.customer_request = "老板已更新"
        save_case_to_disk(case)
        with self.assertRaises(CaseConflictError):
            save_case_to_disk(stale)
        self.assertEqual(load_case_from_disk().customer_request, "老板已更新")
        self.assertEqual(case.storage_revision, 2)

    def test_concurrent_updates_conflict_cleanly_without_missing_temp_file(self):
        save_case_to_disk(TravelCase(customer_request="测试"))
        copies = [load_case_from_disk(), load_case_from_disk()]
        gate = Barrier(2)
        def update(item):
            gate.wait(timeout=5)
            try:
                save_case_to_disk(item)
                return "saved"
            except CaseConflictError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, copies))
        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertEqual(load_case_from_disk().storage_revision, 2)
        self.assertFalse(list(self.base.glob("*.tmp")))

    def test_deleted_case_cannot_be_resurrected_and_stale_delete_is_rejected(self):
        case = TravelCase(customer_request="测试")
        save_case_to_disk(case)
        stale = load_case_from_disk()
        save_case_to_disk(case)
        with self.assertRaises(CaseConflictError):
            delete_case_from_disk(stale)
        delete_case_from_disk(case)
        with self.assertRaises(CaseConflictError):
            save_case_to_disk(case)
        self.assertIsNone(load_case_from_disk())

    def test_legacy_case_without_revision_can_be_upgraded(self):
        (self.base / "current_case.json").write_text('{"id":"legacy","customer_request":"old"}', encoding="utf-8")
        case = load_case_from_disk()
        self.assertEqual(case.storage_revision, 0)
        save_case_to_disk(case)
        self.assertEqual(load_case_from_disk().storage_revision, 1)

    def test_separate_processes_also_reject_stale_save(self):
        save_case_to_disk(TravelCase(customer_request="跨进程测试"))
        source = (
            "import sys; from pathlib import Path; import src.storage as s; "
            f"s.CASE_FILE=Path({str(self.base / 'current_case.json')!r}); "
            "c=s.load_case_from_disk(); print('ready', flush=True); sys.stdin.readline(); "
            "\ntry:\n s.save_case_to_disk(c); print('saved', flush=True)"
            "\nexcept s.CaseConflictError:\n print('conflict', flush=True)"
        )
        processes = [subprocess.Popen([sys.executable, "-c", source], cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), "ready")
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            results = [process.communicate(timeout=15)[0].strip() for process in processes]
            self.assertCountEqual(results, ["saved", "conflict"])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
