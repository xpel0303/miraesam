"""스탬프 챌린지 자료 만들기 화면: 카드 · 안내문 · 포스터 PDF."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import stamp
from ..db import get_db
from ..jobs import enqueue
from ..models import ClassGroup, Job, Teacher
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/stamp")


def _last_settings(db: Session, aid: int) -> dict:
    job = db.execute(
        select(Job).where(Job.academy_id == aid, Job.kind == "stamp_pdf").order_by(Job.id.desc()).limit(1)
    ).scalar_one_or_none()
    return stamp.clean((job.payload or {}).get("settings", {})) if job else dict(stamp.DEFAULTS)


@router.get("")
def stamp_home(request: Request, job: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    aid = teacher.academy_id
    classes = list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == aid, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())
    return render(
        request,
        "stamp/index.html",
        {
            "teacher": teacher,
            "s": _last_settings(db, aid),
            "accents": stamp.ACCENTS,
            "docs": stamp.DOCS,
            "classes": classes,
            "pdf_job": job,
            "active": "stamp",
        },
    )


@router.post("/pdf")
async def stamp_pdf(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    form = await request.form()
    docs = [d for d, _ in stamp.DOCS if d in form.getlist("docs")]
    if not docs:
        flash(request, "만들 자료를 하나 이상 골라 주세요.", "error")
        return RedirectResponse("/stamp", status_code=303)
    class_id = str(form.get("class_id") or "")
    if class_id.isdigit():
        cg = db.get(ClassGroup, int(class_id))
        class_id = cg.id if cg and cg.academy_id == teacher.academy_id else None
    else:
        class_id = None
    blank = str(form.get("blank") or "1")
    payload = {
        "settings": stamp.clean(dict(form)),
        "docs": docs,
        "class_id": class_id,
        "blank": max(1, min(int(blank) if blank.isdigit() else 1, 60)),
    }
    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="stamp_pdf", title="스탬프 챌린지 자료", payload=payload)
    return RedirectResponse(f"/stamp?job={job.id}", status_code=303)
