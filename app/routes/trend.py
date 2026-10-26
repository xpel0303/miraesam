"""학교 경향분석 화면."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..exam import spec as exam_spec
from ..jobs import enqueue
from ..mock import trend
from ..models import ExamPaper, Job, MockSet, School, SchoolTrend, Teacher
from ..web import current_teacher, render

router = APIRouter(prefix="/trend")


def _go(url):
    return RedirectResponse(url, status_code=303)


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    rows = db.execute(select(School, ExamPaper.grade, func.count(ExamPaper.id))
                      .join(ExamPaper, ExamPaper.school_id == School.id)
                      .where(ExamPaper.academy_id == teacher.academy_id, ExamPaper.status == "done")
                      .group_by(School.id, ExamPaper.grade).order_by(School.name, ExamPaper.grade)).all()
    memos = {(t.school_id, t.grade): t for t in db.execute(select(SchoolTrend).where(SchoolTrend.academy_id == teacher.academy_id)).scalars()}
    no_school = db.execute(select(func.count(ExamPaper.id)).where(ExamPaper.academy_id == teacher.academy_id, ExamPaper.status == "done",
                                                                  ExamPaper.school_id.is_(None))).scalar()
    items = [{"school": s, "grade": g or "", "count": n, "memo": memos.get((s.id, g or ""))} for s, g, n in rows]
    return render(request, "trend/index.html", {"teacher": teacher, "items": items, "no_school": no_school, "active": "trend"})


def _papers(db, teacher, school_id: int, grade: str) -> list[ExamPaper]:
    q = select(ExamPaper).where(ExamPaper.academy_id == teacher.academy_id, ExamPaper.school_id == school_id, ExamPaper.status == "done")
    if grade:
        q = q.where(ExamPaper.grade == grade)
    return list(db.execute(q.order_by(ExamPaper.year, ExamPaper.semester, ExamPaper.term, ExamPaper.id)).scalars())


def _row(db, teacher, school_id: int, grade: str) -> SchoolTrend | None:
    return db.execute(select(SchoolTrend).where(SchoolTrend.academy_id == teacher.academy_id, SchoolTrend.school_id == school_id,
                                                SchoolTrend.grade == grade)).scalar_one_or_none()


@router.get("/{school_id}")
def detail(school_id: int, request: Request, grade: str = "", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    school = db.get(School, school_id)
    if school is None or school.academy_id != teacher.academy_id:
        raise HTTPException(404, "학교를 찾을 수 없습니다.")
    papers = _papers(db, teacher, school_id, grade)
    if not papers:
        raise HTTPException(404, "이 학교의 기출 분석이 없습니다.")
    row = _row(db, teacher, school_id, grade)
    job = db.get(Job, row.last_job_id) if row and row.last_job_id else None
    busy = bool(job and job.status in ("queued", "running"))
    stale = bool(row and row.memo and set(row.exam_ids or []) != {p.id for p in papers})
    mocks = list(db.execute(select(MockSet).where(MockSet.academy_id == teacher.academy_id, MockSet.school_id == school_id)
                            .order_by(MockSet.id.desc()).limit(10)).scalars())
    return render(request, "trend/detail.html", {
        "teacher": teacher, "school": school, "grade": grade, "papers": papers, "cmp": trend.compare(papers), "row": row,
        "job": job, "busy": busy, "stale": stale, "names": trend.MEMO_NAMES, "keys": trend.MEMO_KEYS, "fmt": exam_spec._f,
        "mocks": mocks, "active": "trend"})


@router.post("/{school_id}/memo")
async def make_memo(school_id: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    grade = str(f.get("grade", ""))[:6]
    school = db.get(School, school_id)
    if school is None or school.academy_id != teacher.academy_id:
        raise HTTPException(404)
    row = _row(db, teacher, school_id, grade)
    if row is None:
        row = SchoolTrend(academy_id=teacher.academy_id, school_id=school_id, grade=grade)
        db.add(row)
        db.flush()
    job = db.get(Job, row.last_job_id) if row.last_job_id else None
    if not (job and job.status in ("queued", "running")):
        job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="trend_memo",
                      title=f"학교 경향분석 · {school.name} {grade}".strip(), payload={"trend_id": row.id})
        row.last_job_id = job.id
    db.commit()
    return _go(f"/trend/{school_id}" + (f"?grade={grade}" if grade else ""))
