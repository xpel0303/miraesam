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
    }
