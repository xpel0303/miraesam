"""학생 명단: 학교 · 반 · 학생."""
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..jobs import enqueue
from ..models import ClassGroup, School, Student, Teacher
from ..security import make_code
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/roster")

GRADES = ["초4", "초5", "초6", "중1", "중2", "중3", "고1", "고2", "고3"]
LEVELS = ["초", "중", "고"]


def _back(tab: str = "students", **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    return RedirectResponse(f"/roster?tab={tab}" + (f"&{qs}" if qs else ""), status_code=303)


def _own(db: Session, model, obj_id: int, teacher: Teacher):
    obj = db.get(model, obj_id)
    if obj is None or obj.academy_id != teacher.academy_id:
        raise HTTPException(404, "찾을 수 없습니다.")
    return obj


def _new_code(db: Session) -> str:
    code = make_code(6)
    while db.execute(select(Student.id).where(Student.access_code == code)).first():
        code = make_code(6)
    return code


def _norm_grade(text: str) -> str | None:
    t = text.replace(" ", "").replace("학년", "")
    m = re.match(r"^(초|중|고)?(\d)$", t)
    if not m:
        return t if t in GRADES else None
    level, n = m.group(1) or "고", m.group(2)
    g = f"{level}{n}"
    return g if g in GRADES else None


def _school_by_name(db: Session, aid: int, name: str, grade: str | None) -> School | None:
    name = name.strip()
    if not name:
        return None
    s = db.execute(select(School).where(School.academy_id == aid, School.name == name)).scalar_one_or_none()
    if s is None:
        level = grade[0] if grade else ("중" if "중" in name else "고")
        s = School(academy_id=aid, name=name, level=level)
        db.add(s)
        db.flush()
    return s


def _class_by_name(db: Session, aid: int, name: str) -> ClassGroup | None:
    name = name.strip()
    if not name:
        return None
    c = db.execute(select(ClassGroup).where(ClassGroup.academy_id == aid, ClassGroup.name == name)).scalar_one_or_none()
    if c is None:
        c = ClassGroup(academy_id=aid, name=name)
        db.add(c)
        db.flush()
    return c


@router.get("")
def roster(
    request: Request,
    tab: str = "students",
    class_id: str = "",
    school_id: str = "",
    q: str = "",
    inactive: str = "",
    job: int | None = None,
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    aid = teacher.academy_id
    schools = list(db.execute(select(School).where(School.academy_id == aid).order_by(School.level.desc(), School.name)).scalars())
    classes = list(
        db.execute(
            select(ClassGroup).options(selectinload(ClassGroup.teacher)).where(ClassGroup.academy_id == aid).order_by(ClassGroup.active.desc(), ClassGroup.name)
        ).scalars()
    )
    teachers = list(db.execute(select(Teacher).where(Teacher.academy_id == aid).order_by(Teacher.name)).scalars())

    sq = (
        select(Student)
        .options(selectinload(Student.school), selectinload(Student.class_group))
        .where(Student.academy_id == aid)
        .order_by(Student.name)
    )
    if not inactive:
        sq = sq.where(Student.active.is_(True))
    if class_id.isdigit():
        sq = sq.where(Student.class_id == int(class_id))
    elif class_id == "none":
        sq = sq.where(Student.class_id.is_(None))
    if school_id.isdigit():
        sq = sq.where(Student.school_id == int(school_id))
    if q.strip():
        sq = sq.where(Student.name.contains(q.strip()))
    students = list(db.execute(sq).scalars())

    class_counts = dict(
        db.execute(
            select(Student.class_id, func.count()).where(Student.academy_id == aid, Student.active.is_(True)).group_by(Student.class_id)
        ).all()
    )
    school_counts = dict(
        db.execute(
            select(Student.school_id, func.count()).where(Student.academy_id == aid, Student.active.is_(True)).group_by(Student.school_id)
        ).all()
    )
    return render(
        request,
        "roster/index.html",
        {
            "teacher": teacher,
            "tab": tab,
            "schools": schools,
            "classes": classes,
            "teachers": teachers,
            "students": students,
            "class_counts": class_counts,
            "school_counts": school_counts,
            "filters": {"class_id": class_id, "school_id": school_id, "q": q, "inactive": inactive},
            "grades": GRADES,
            "levels": LEVELS,
            "pdf_job": job,
            "active": "roster",
        },
    )


# ---------- 학생 ----------

@router.post("/students")
def add_student(
    request: Request,
    name: str = Form(...),
    school_id: str = Form(""),
    grade: str = Form(""),
    class_id: str = Form(""),
    parent_phone: str = Form(""),
    memo: str = Form(""),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    if not name.strip():
        flash(request, "학생 이름을 써 주세요.", "error")
        return _back()
    s = Student(
        academy_id=teacher.academy_id,
        name=name.strip()[:40],
        school_id=_own(db, School, int(school_id), teacher).id if school_id.isdigit() else None,
        grade=grade if grade in GRADES else None,
        class_id=_own(db, ClassGroup, int(class_id), teacher).id if class_id.isdigit() else None,
        parent_phone=parent_phone.strip() or None,
        memo=memo.strip() or None,
        access_code=_new_code(db),
    )
    db.add(s)
    db.commit()
    flash(request, f"{s.name} 학생을 추가했습니다. 접속 코드는 {s.access_code}입니다.")
    return _back()


@router.post("/students/bulk")
def bulk_students(request: Request, text: str = Form(...), teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """한 줄에 한 명: 이름, 학교, 학년, 반  (쉼표 또는 엑셀에서 복사한 탭 구분)."""
    aid = teacher.academy_id
    added, skipped = 0, []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        cols = [c.strip() for c in re.split(r"\t|,", line)]
        cols += [""] * (4 - len(cols))
        name, school_name, grade_text, class_name = cols[:4]
        if not name or name in ("이름", "학생"):
            continue
        grade = _norm_grade(grade_text) if grade_text else None
        if grade_text and grade is None:
            skipped.append(f"{lineno}줄({name}): 학년 '{grade_text}'을 알 수 없음")
            continue
        db.add(
            Student(
                academy_id=aid,
                name=name[:40],
                school_id=(_school_by_name(db, aid, school_name, grade) or School()).id if school_name else None,
                grade=grade,
                class_id=(_class_by_name(db, aid, class_name) or ClassGroup()).id if class_name else None,
                access_code=_new_code(db),
            )
        )
        db.flush()
        added += 1
    db.commit()
    msg = f"학생 {added}명을 추가했습니다."
    if skipped:
        msg += " 건너뛴 줄: " + "; ".join(skipped[:5]) + (" 외" if len(skipped) > 5 else "")
    flash(request, msg, "error" if skipped and not added else "ok")
    return _back()


@router.post("/students/{sid}/edit")
def edit_student(
    sid: int,
    request: Request,
    name: str = Form(...),
    school_id: str = Form(""),
    grade: str = Form(""),
    class_id: str = Form(""),
    parent_phone: str = Form(""),
    memo: str = Form(""),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    s = _own(db, Student, sid, teacher)
    if name.strip():
        s.name = name.strip()[:40]
    s.school_id = _own(db, School, int(school_id), teacher).id if school_id.isdigit() else None
    s.grade = grade if grade in GRADES else None
    s.class_id = _own(db, ClassGroup, int(class_id), teacher).id if class_id.isdigit() else None
    s.parent_phone = parent_phone.strip() or None
    s.memo = memo.strip() or None
    db.commit()
    flash(request, f"{s.name} 학생 정보를 고쳤습니다.")
    return _back()


@router.post("/students/{sid}/toggle")
def toggle_student(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, Student, sid, teacher)
    s.active = not s.active
    db.commit()
    flash(request, f"{s.name} 학생을 {'다시 재원으로' if s.active else '퇴원으로'} 바꿨습니다.")
    return _back(inactive="" if s.active else "1")


@router.post("/students/{sid}/code")
def new_code(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, Student, sid, teacher)
    s.access_code = _new_code(db)
    db.commit()
    flash(request, f"{s.name} 학생의 새 접속 코드는 {s.access_code}입니다.")
    return _back()


@router.post("/pdf")
def roster_pdf(request: Request, class_id: str = Form(""), teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    payload = {"class_id": _own(db, ClassGroup, int(class_id), teacher).id} if class_id.isdigit() else {}
    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="roster_pdf", title="학생 명단 PDF", payload=payload)
    return _back(class_id=class_id, job=job.id)


# ---------- 반 ----------

@router.post("/classes")
def add_class(
    request: Request,
    name: str = Form(...),
    teacher_id: str = Form(""),
    schedule: str = Form(""),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    if not name.strip():
        flash(request, "반 이름을 써 주세요.", "error")
        return _back("classes")
    if db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.name == name.strip())).first():
        flash(request, "같은 이름의 반이 이미 있습니다.", "error")
        return _back("classes")
    db.add(
        ClassGroup(
            academy_id=teacher.academy_id,
            name=name.strip()[:60],
            teacher_id=_own(db, Teacher, int(teacher_id), teacher).id if teacher_id.isdigit() else None,
            schedule=schedule.strip() or None,
        )
    )
    db.commit()
    flash(request, f"'{name.strip()}' 반을 만들었습니다.")
    return _back("classes")


@router.post("/classes/{cid}/edit")
def edit_class(
    cid: int,
    request: Request,
    name: str = Form(...),
    teacher_id: str = Form(""),
    schedule: str = Form(""),
    active: str = Form(""),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    c = _own(db, ClassGroup, cid, teacher)
    if name.strip():
        c.name = name.strip()[:60]
    c.teacher_id = _own(db, Teacher, int(teacher_id), teacher).id if teacher_id.isdigit() else None
    c.schedule = schedule.strip() or None
    c.active = bool(active)
    db.commit()
    flash(request, f"'{c.name}' 반을 고쳤습니다.")
    return _back("classes")


# ---------- 학교 ----------

@router.post("/schools")
def add_school(request: Request, name: str = Form(...), level: str = Form("고"), teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        flash(request, "학교 이름을 써 주세요.", "error")
        return _back("schools")
    if db.execute(select(School).where(School.academy_id == teacher.academy_id, School.name == name)).first():
        flash(request, "이미 등록된 학교입니다.", "error")
        return _back("schools")
    db.add(School(academy_id=teacher.academy_id, name=name[:60], level=level if level in LEVELS else "고"))
    db.commit()
    flash(request, f"{name}를 등록했습니다.")
    return _back("schools")


@router.post("/schools/{sid}/edit")
def edit_school(sid: int, request: Request, name: str = Form(...), level: str = Form("고"), teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, School, sid, teacher)
    if name.strip():
        s.name = name.strip()[:60]
    if level in LEVELS:
        s.level = level
    db.commit()
    flash(request, f"{s.name} 정보를 고쳤습니다.")
    return _back("schools")


@router.post("/schools/{sid}/delete")
def delete_school(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, School, sid, teacher)
    used = db.scalar(select(func.count()).select_from(Student).where(Student.school_id == s.id))
    if used:
        flash(request, f"{s.name}에 학생 {used}명이 있어 지울 수 없습니다. 학생의 학교를 먼저 바꿔 주세요.", "error")
        return _back("schools")
    db.delete(s)
    db.commit()
    flash(request, f"{s.name}를 지웠습니다.")
    return _back("schools")
