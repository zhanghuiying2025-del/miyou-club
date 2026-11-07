from pathlib import Path
from contextlib import contextmanager
from threading import RLock
from uuid import uuid4
import os
import json
import time
from datetime import datetime, timezone

from src.models import TravelCase


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
CASE_FILE = DATA_DIR / "current_case.json"
_LOCK = RLock()


class CaseConflictError(RuntimeError):
    """A newer save or deletion must not be overwritten by a stale session."""


@contextmanager
def _write_lock():
    CASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, CASE_FILE.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_book():
    try:
        # On Windows an open reader can deny the writer's atomic replace.
        with _LOCK:
            text=CASE_FILE.read_text(encoding="utf-8")
        raw=json.loads(text)
    except FileNotFoundError:
        return {"format":"case_book_v1","current_id":None,"cases":{},"visits":{}}
    if raw.get("format")=="case_book_v1":
        return raw
    # Read legacy single-case files without changing them until the next save.
    legacy=TravelCase.model_validate(raw)
    return {"format":"case_book_v1","current_id":legacy.id,
        "cases":{legacy.id:{"case":legacy.model_dump(mode="json"),"updated_at":""}},"visits":{}}


def _write_book(book):
    temporary=CASE_FILE.with_name(f"current_case_{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(book,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(CASE_FILE)
                break
            except PermissionError as exc:
                # Another local process can briefly have the file open.
                if getattr(exc, "winerror", None) not in {5, 32} or attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def save_case_to_disk(case: TravelCase) -> None:
    with _write_lock():
        book=_read_book()
        record=book["cases"].get(case.id)
        current=TravelCase.model_validate(record["case"]) if record else None
        if current is not None and current.storage_revision!=case.storage_revision:
            raise CaseConflictError("案件已在另一页面更新，请使用最新状态重新操作")
        if current is None and case.storage_revision>0:
            raise CaseConflictError("案件已被删除，旧页面不能重新保存")
        revision=case.storage_revision+1
        snapshot=case.model_copy(update={"storage_revision":revision})
        book["cases"][case.id]={"case":snapshot.model_dump(mode="json"),"updated_at":datetime.now(timezone.utc).isoformat()}
        book["current_id"]=case.id
        _write_book(book)
        case.storage_revision=revision


def load_case_from_disk(case_id: str | None = None) -> TravelCase | None:
    book=_read_book()
    record=book["cases"].get(case_id or book["current_id"])
    return TravelCase.model_validate(record["case"]) if record else None


def list_saved_cases():
    book=_read_book()
    return sorted([{"id":key,"updated_at":record["updated_at"],
        "status":record["case"].get("status","new"),"service_mode":record["case"].get("service_mode","travel_planning"),
        "title":(record["case"].get("requirements") or {}).get("event_name") or record["case"].get("customer_request","")[:60],
        "revision":record["case"].get("storage_revision",0)} for key,record in book["cases"].items()],
        key=lambda r:r["updated_at"],reverse=True)


def saved_case_snapshots():
    """One consistent read of all full archives; no destructive migration."""
    return list(_read_book()["cases"].values())


def hidden_archive_ids() -> set[str]:
    return set(_read_book().get("hidden_archive_ids", []))


def set_archive_hidden(document_id: str, hidden: bool) -> None:
    """Save a list preference separately from the source records."""
    with _write_lock():
        book = _read_book()
        previous = set(book.get("hidden_archive_ids", []))
        updated = previous | {document_id} if hidden else previous - {document_id}
        if updated != previous:
            book["hidden_archive_ids"] = sorted(updated)
            _write_book(book)


def load_visit(visit_id):
    return dict(_read_book()["visits"].get(visit_id,{}))


def save_visit(visit_id,changes):
    if not isinstance(visit_id,str) or not 8<=len(visit_id)<=100:
        raise ValueError("无效访问编号")
    with _write_lock():
        book=_read_book()
        current=book["visits"].get(visit_id,{})
        updated={**current,**changes}
        if updated!=current:
            book["visits"][visit_id]=updated
            _write_book(book)


def delete_case_from_disk(expected_case: TravelCase | None = None) -> None:
    with _write_lock():
        book=_read_book()
        key=expected_case.id if expected_case else book["current_id"]
        record=book["cases"].get(key)
        current=TravelCase.model_validate(record["case"]) if record else None
        if expected_case and current and current.storage_revision!=expected_case.storage_revision:
            raise CaseConflictError("案件已更新，请刷新后再删除")
        if record:
            del book["cases"][key]
            for view in book["visits"].values():
                for field in ("customer_case_id","owner_case_id"):
                    if view.get(field)==key:
                        view[field]=None
            if book["current_id"]==key:
                book["current_id"]=next(reversed(book["cases"]),None)
            _write_book(book)

