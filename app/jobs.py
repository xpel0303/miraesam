"""작업 대기열.

오래 걸리는 일(시험지 읽기, 분석지 만들기 등)은 요청 안에서 처리하지 않고
jobs 테이블에 넣은 뒤 백그라운드 일꾼이 하나씩 처리합니다.

- 화면은 /api/jobs/{id} 를 몇 초마다 확인해 진행 단계를 보여줍니다.
- 서버가 재시작되면 '실행 중'이던 작업을 다시 대기열에 넣어 이어서 처리합니다
  (Render 무료 서버가 잠들었다 깨어나도 작업이 사라지지 않음).

새 작업 종류 만들기:
    @job_handler("exam_extract")
    def run(ctx: JobContext) -> dict:
        ctx.step("1/6쪽 읽는 중", 10)
        ...
        return {"file_id": ctx.save_file("분석지.pdf", "application/pdf", pdf_bytes)}
"""
import logging
import threading
import traceback
from typing import Callable

from sqlalchemy import select

from .config import DEFAULT_GEMINI_MODEL, JOB_WORKERS
from .db import session_scope
from .llm import LLMError
from .models import Job, StoredFile, Teacher, now
from .security import decrypt

log = logging.getLogger("jobs")

MAX_ATTEMPTS = 3
_HANDLERS: dict[str, Callable[["JobContext"], dict | None]] = {}
_stop = threading.Event()
_wake = threading.Event()
_threads: list[threading.Thread] = []


def job_handler(kind: str):
    def deco(fn):
        _HANDLERS[kind] = fn
        return fn
    return deco


class JobContext:
    def __init__(self, job: Job):
        self.job_id = job.id
        self.kind = job.kind
        self.payload = dict(job.payload or {})
        self.academy_id = job.academy_id
        self.teacher_id = job.teacher_id

    def step(self, text: str, progress: int | None = None):
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            job.step = text
            if progress is not None:
                job.progress = max(0, min(100, progress))

    def save_file(self, filename: str, mime: str, data: bytes) -> int:
        with session_scope() as db:
            f = StoredFile(academy_id=self.academy_id, filename=filename, mime=mime, data=data)
            db.add(f)
            db.flush()
            return f.id

    def gemini(self) -> tuple[str | None, str]:
        """(API 키, 모델) — 작업을 맡긴 선생님의 설정."""
        with session_scope() as db:
            t = db.get(Teacher, self.teacher_id) if self.teacher_id else None
            if t is None:
                return None, DEFAULT_GEMINI_MODEL
            return decrypt(t.gemini_key_enc), (t.gemini_model or DEFAULT_GEMINI_MODEL)


def enqueue(db, *, academy_id: int, teacher_id: int | None, kind: str, title: str, payload: dict) -> Job:
    if kind not in _HANDLERS:
        raise ValueError(f"등록되지 않은 작업 종류: {kind}")
    job = Job(academy_id=academy_id, teacher_id=teacher_id, kind=kind, title=title, payload=payload)
    db.add(job)
    db.commit()
    _wake.set()
    return job


def _claim_next() -> int | None:
    with session_scope() as db:
        stmt = (
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = db.execute(stmt).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.step = "시작하는 중"
        job.error = None
        return job.id


def _run(job_id: int):
    with session_scope() as db:
        job = db.get(Job, job_id)
        ctx = JobContext(job)
        attempts = job.attempts
    handler = _HANDLERS.get(ctx.kind)
    try:
        if handler is None:
            raise RuntimeError(f"처리할 수 없는 작업 종류입니다: {ctx.kind}")
        result = handler(ctx) or {}
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status, job.step, job.progress, job.result = "done", "완료", 100, result
    except LLMError as e:
        _fail(job_id, str(e))
    except Exception as e:  # 예상 못 한 오류: 기록하고, 횟수가 남았으면 다시 대기열로
        log.error("job %s failed: %s", job_id, traceback.format_exc())
        if attempts < MAX_ATTEMPTS:
            with session_scope() as db:
                job = db.get(Job, job_id)
                job.status, job.step = "queued", f"오류가 나서 다시 시도합니다 ({attempts}/{MAX_ATTEMPTS})"
        else:
            _fail(job_id, f"작업 중 오류가 났습니다: {e}")


def _fail(job_id: int, message: str):
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.status, job.step, job.error = "failed", "실패", message


def _worker():
    while not _stop.is_set():
        try:
            job_id = _claim_next()
        except Exception:
            log.error("claim failed: %s", traceback.format_exc())
            job_id = None
        if job_id is None:
            _wake.wait(2.0)
            _wake.clear()
            continue
        _run(job_id)


def start_workers():
    # 재시작 전에 '실행 중'이던 작업은 다시 대기열로
    with session_scope() as db:
        for job in db.execute(select(Job).where(Job.status == "running")).scalars():
            if job.attempts < MAX_ATTEMPTS:
                job.status, job.step = "queued", "서버가 다시 시작되어 이어서 처리합니다"
            else:
                job.status, job.step, job.error = "failed", "실패", "여러 번 시도했지만 끝내지 못했습니다."
            job.updated_at = now()
    _stop.clear()
    for i in range(max(1, JOB_WORKERS)):
        t = threading.Thread(target=_worker, name=f"job-worker-{i}", daemon=True)
        t.start()
        _threads.append(t)
    k = threading.Thread(target=_keepalive, name="job-keepalive", daemon=True)
    k.start()
    _threads.append(k)


def _keepalive():
    """무료 서버는 15분 동안 들어오는 요청이 없으면 잠들어 작업이 멈춤 → 작업이 남아 있는 동안 4분마다 자기 주소를 두드림."""
    import os
    import urllib.request

    base = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SITE_URL")
    if not base:
        return
    while not _stop.wait(240):
        try:
            with session_scope() as db:
                busy = db.execute(select(Job.id).where(Job.status.in_(("queued", "running"))).limit(1)).first()
            if busy:
                urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=20).read()
        except Exception:  # noqa: BLE001
            log.warning("keepalive ping failed")


def stop_workers():
    _stop.set()
    _wake.set()
