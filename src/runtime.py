"""One Streamlit server shares in-flight jobs across browser sessions.

Business truth remains in versioned storage. Workers receive snapshots and never
call Streamlit. This is not a durable queue across server restarts or processes.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, RLock, local
import time


@dataclass
class Job:
    key: tuple
    case_id: str
    future: object
    started: float = field(default_factory=time.monotonic)
    applied: bool = False
    lock: object = field(default_factory=RLock)
    events: list = field(default_factory=list)
    pause: Event = field(default_factory=Event)


_LOCK = RLock()
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="travel-job")
_JOBS = {}
_CURRENT = local()


def submit_once(key, case_id, function, *args):
    with _LOCK:
        job = _JOBS.get(key)
        if job is None:
            # Keep active results available through refresh; trim only old applied work.
            for old_key in list(_JOBS):
                old = _JOBS[old_key]
                if len(_JOBS) > 100 and old.applied and old.future.done():
                    del _JOBS[old_key]
            job = Job(key=key, case_id=case_id, future=None)
            _JOBS[key] = job
            def execute():
                _CURRENT.job = job
                try:
                    return function(*args)
                finally:
                    _CURRENT.job = None
            job.future = _POOL.submit(execute)
        return job


def consume(job, apply):
    """Only one session commits a result. Failed commits leave it available to retry."""
    with job.lock:
        if job.applied or not job.future.done():
            return False
        apply(job.future.result())
        job.applied = True
        return True


def active_jobs(case_id):
    with _LOCK:
        return [j for j in _JOBS.values() if j.case_id == case_id and not j.future.done()]


def pause_planning(case_id):
    for job in active_jobs(case_id):
        if job.key[0] == "planning":
            job.pause.set()


def planning_paused():
    job = getattr(_CURRENT, "job", None)
    return bool(job and job.pause.is_set())


def emit_progress(actor, task, finished=False):
    job = getattr(_CURRENT, "job", None)
    if job:
        with job.lock:
            job.events.append({"actor": actor, "stage": "已完成" if finished else "处理中",
                "message": f"我{'已完成' if finished else '正在'}{task}。"})
            job.events = job.events[-12:]


def progress_events(job):
    with job.lock:
        return list(job.events)
