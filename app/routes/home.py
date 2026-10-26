from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..features import BY_KEY, FEATURES, STAGE_NAMES
from ..models import ClassGroup, Job, School, StoredFile, Student, Teacher
from ..web import current_teacher, render

router = APIRouter()


@router.get("/")
def home(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    aid = teacher.academy_id
    counts = {
        "students": db.scalar(select(func.count()).select_from(Student).where(Student.academy_id == aid, Student.active.is_(True))),
        "classes": db.scalar(select(func.count()).select_from(ClassGroup).where(ClassGroup.academy_id == aid, ClassGroup.active.is_(True))),
        "schools": db.scalar(select(func.count()).select_from(School).where(School.academy_id == aid)),
    }
    recent_jobs = list(
        db.execute(select(Job).where(Job.academy_id == aid).order_by(Job.id.desc()).limit(5)).scalars()
    )
    stages = []
    for n, label in STAGE_NAMES.items():
        items = [f for f in FEATURES if f.stage == n]
        stages.append({"n": n, "label": label, "features": items, "done": sum(f.ready for f in items)})
    return render(
        request,
        "home.html",
        {
            "teacher": teacher,
            "counts": counts,
            "recent_jobs": recent_jobs,
            "stages": stages,
            "needs_key": not teacher.gemini_key_enc,
            "active": "home",
        },
    )


@router.get("/soon/{key}")
def soon(key: str, request: Request, teacher: Teacher = Depends(current_teacher)):
    feature = BY_KEY.get(key)
    if feature is None:
        raise HTTPException(404)
    return render(request, "soon.html", {"teacher": teacher, "feature": feature, "active": key})


@router.get("/files/{file_id}")
def download(file_id: int, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = db.get(StoredFile, file_id)
    if f is None or f.academy_id != teacher.academy_id:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    from urllib.parse import quote

    return Response(
        f.data,
        media_type=f.mime,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(f.filename)}", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/api/jobs/{job_id}")
def job_status(job_id: int, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.academy_id != teacher.academy_id:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "step": job.step,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        **_estimate(db, job),
    }


def _estimate(db: Session, job: Job) -> dict:
    """예상 남은 시간(초)과 대기 순서.
    돌고 있으면: 지금까지 걸린 시간과 진행률로 계산 (진행이 조금이라도 쌓인 뒤부터).
    아직이면: 같은 종류 작업이 최근에 걸린 시간의 가운데값."""
    from datetime import datetime, timezone
    from statistics import median

    out: dict = {"eta": None, "eta_kind": None, "ahead": 0, "elapsed": None}
    now_ = datetime.now(timezone.utc)
    if job.status == "queued":
        out["ahead"] = db.query(Job).filter(Job.status.in_(("queued", "running")), Job.id < job.id).count()
    if job.status not in ("queued", "running"):
        return out
    if job.status == "running" and job.started_at:
        started = job.started_at if job.started_at.tzinfo else job.started_at.replace(tzinfo=timezone.utc)
        elapsed = (now_ - started).total_seconds()
        out["elapsed"] = int(elapsed)
        done = (job.progress or 0) - (job.start_progress or 0)
        if done >= 8 and elapsed >= 15:
            left = elapsed * (100 - job.progress) / done
            out["eta"], out["eta_kind"] = int(max(10, left)), "live"
            return out
    rows = (db.query(Job.started_at, Job.finished_at).filter(Job.kind == job.kind, Job.status == "done", Job.finished_at.isnot(None),
                                                            Job.started_at.isnot(None), Job.academy_id == job.academy_id)
            .order_by(Job.id.desc()).limit(15).all())
    secs = [(f - s).total_seconds() for s, f in rows if f and s and f > s]
    if secs:
        typical = median(secs)
        spent = out["elapsed"] or 0
        out["eta"], out["eta_kind"] = int(max(10, typical - spent)), "typical"
    return out
