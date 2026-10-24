"""기출 분석 (선생님 화면): 시험지 올리기 → 문항 확인·수정 → 분석지 PDF / 학생·반 리포트로 보내기."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..exam import pages as pg
from ..exam import spec
from ..jobs import enqueue
from ..models import Exam, ExamPaper, Job, School, StoredFile, Teacher
from ..reports import spec as rspec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/exam")
GRADES = ["고1", "고2", "고3", "중1", "중2", "중3"]
MAX_UPLOAD = 80 * 1024 * 1024


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, pid: int, teacher: Teacher) -> ExamPaper:
    p = db.get(ExamPaper, pid)
    if p is None or p.academy_id != teacher.academy_id:
        raise HTTPException(404, "시험지를 찾을 수 없습니다.")
    return p


def _busy(db: Session, p: ExamPaper) -> bool:
    job = db.get(Job, p.last_job_id) if p.last_job_id else None
    return bool(job and job.status in ("queued", "running") and job.kind == "exam_extract")


def _schools(db: Session, teacher: Teacher) -> list[School]:
    return list(db.execute(select(School).where(School.academy_id == teacher.academy_id).order_by(School.name)).scalars())


def _int(v, lo, hi):
    try:
        x = int(str(v).strip())
        return x if lo <= x <= hi else None
    except (TypeError, ValueError):
        return None


def _title(p: ExamPaper) -> str:
    from ..handlers.exam import auto_title
    return auto_title(p)


def _start(db: Session, p: ExamPaper, teacher: Teacher, title: str):
    job = enqueue(db, academy_id=p.academy_id, teacher_id=teacher.id, kind="exam_extract", title=f"기출 분석 · {title}", payload={"paper_id": p.id})
    p.last_job_id = job.id
    p.status = "waiting"
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    papers = list(db.execute(select(ExamPaper).options(selectinload(ExamPaper.school), selectinload(ExamPaper.teacher))
                             .where(ExamPaper.academy_id == teacher.academy_id)
                             .order_by(ExamPaper.year.desc(), ExamPaper.semester.desc(), ExamPaper.term.desc(), ExamPaper.id.desc()).limit(300)).scalars())
    by_school: dict[str, list] = {}
    for p in papers:
        by_school.setdefault(p.school.name if p.school else "학교 미정", []).append(p)
    return render(request, "exam/index.html", {"teacher": teacher, "by_school": by_school, "n": len(papers), "active": "exam"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    return render(request, "exam/new.html", {"teacher": teacher, "form": {"year": 2026, "grade": "고1"}, "schools": _schools(db, teacher),
                                             "grades": GRADES, "active": "exam"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    ups = [u for u in f.getlist("files") if getattr(u, "filename", "")]
    errors, files, size = [], [], 0
    for u in ups:
        data = await u.read()
        size += len(data)
        name = u.filename.lower()
        if not (name.endswith((".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp")) or data[:4] == b"%PDF"):
            errors.append(f"'{u.filename}'은(는) PDF나 사진 파일이 아니에요. 한글(hwp) 파일은 PDF로 저장해서 올려 주세요.")
            continue
        if name.endswith(".heic"):
            errors.append(f"'{u.filename}': 아이폰 HEIC 사진은 아직 못 읽어요. JPG로 바꾸거나 PDF로 스캔해 주세요.")
            continue
        files.append((u.filename, data))
    if not files and not errors:
        errors.append("시험지 파일을 올려 주세요.")
    if size > MAX_UPLOAD:
        errors.append(f"파일이 너무 커요 ({size // (1024 * 1024)}MB). 합쳐서 {MAX_UPLOAD // (1024 * 1024)}MB까지 올릴 수 있어요.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    school_id = _int(f.get("school_id"), 1, 10**9)
    new_school = str(f.get("new_school", "")).strip()[:60]
    grade = str(f.get("grade") or "")
    form = {"school_id": school_id, "new_school": new_school, "grade": grade, "year": f.get("year"), "semester": f.get("semester"), "term": f.get("term")}
    if errors:
        return render(request, "exam/new.html", {"teacher": teacher, "form": form, "errors": errors, "schools": _schools(db, teacher),
                                                 "grades": GRADES, "active": "exam"}, 400)
    if new_school:
        s = db.execute(select(School).where(School.academy_id == teacher.academy_id, School.name == new_school)).scalar_one_or_none()
        if s is None:
            s = School(academy_id=teacher.academy_id, name=new_school, level=grade[:1] if grade[:1] in ("중", "고") else "고")
            db.add(s)
            db.flush()
        school_id = s.id
    elif school_id and not db.execute(select(School.id).where(School.id == school_id, School.academy_id == teacher.academy_id)).first():
        school_id = None
    p = ExamPaper(academy_id=teacher.academy_id, teacher_id=teacher.id, title="", school_id=school_id, grade=grade if grade in GRADES else "",
                  year=_int(f.get("year"), 2000, 2100), semester=_int(f.get("semester"), 1, 2), term=_int(f.get("term"), 1, 2))
    db.add(p)
    db.flush()
    ids = []
    for name, data in files:
        sf = StoredFile(academy_id=teacher.academy_id, filename=name[:200], mime="application/pdf" if name.lower().endswith(".pdf") else "image/jpeg", data=data)
        db.add(sf)
        db.flush()
        ids.append(sf.id)
    p.meta = {"upload_ids": ids, "upload_names": [n for n, _ in files]}
    db.flush()
    db.refresh(p)
    p.title = _title(p)
    _start(db, p, teacher, p.title)
    return _go(f"/exam/{p.id}")


@router.get("/{pid}")
def detail(pid: int, request: Request, pdf_job: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    job = db.get(Job, p.last_job_id) if p.last_job_id else None
    qs = p.questions or []
    st = spec.stats(qs) if qs else None
    report = db.get(Exam, p.report_exam_id) if p.report_exam_id else None
    others = []
    if p.school_id:
        others = list(db.execute(select(ExamPaper).where(ExamPaper.school_id == p.school_id, ExamPaper.id != p.id, ExamPaper.status == "done")
                                 .order_by(ExamPaper.year.desc(), ExamPaper.semester.desc(), ExamPaper.term.desc())).scalars())
    return render(request, "exam/detail.html", {
        "teacher": teacher, "p": p, "job": job, "busy": _busy(db, p), "qs": qs, "st": st, "notes": spec.notes(qs, st) if st else [],
        "types": spec.TYPE_ORDER, "fmt": spec._f, "schools": _schools(db, teacher), "grades": GRADES, "report": report, "others": others,
        "pdf_job": pdf_job, "active": "exam",
    })


@router.post("/{pid}/info")
async def save_info(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    f = await request.form()
    sid = _int(f.get("school_id"), 1, 10**9)
    p.school_id = sid if sid and db.execute(select(School.id).where(School.id == sid, School.academy_id == teacher.academy_id)).first() else None
    p.grade = str(f.get("grade") or "") if f.get("grade") in GRADES else ""
    p.year, p.semester, p.term = _int(f.get("year"), 2000, 2100), _int(f.get("semester"), 1, 2), _int(f.get("term"), 1, 2)
    db.flush()
    db.refresh(p)
    p.title = str(f.get("title", "")).strip()[:120] or _title(p)
    db.commit()
    flash(request, "저장했어요.")
    return _go(f"/exam/{pid}")


def _q_from_form(f, old: dict) -> dict:
    kind = "sa" if f.get("kind") == "sa" else "mc"
    choices = [c.strip() for c in str(f.get("choices", "")).splitlines() if c.strip()]
    choices = [re.sub(r"^[①②③④⑤]\s*", "", c) for c in choices]
    pts = str(f.get("points", "")).strip()
    q = {**old, "label": spec.norm_label(f.get("label") or old.get("label", ""), kind) or old.get("label", "?"), "kind": kind,
         "points": rspec.num(pts) if pts else None, "stem": str(f.get("stem", "")).strip(), "group": str(f.get("group", "")).strip(),
         "passage": str(f.get("passage", "")).strip(), "box": str(f.get("box", "")).strip(), "choices": choices,
         "conditions": str(f.get("conditions", "")).strip(), "point": str(f.get("point", "")).strip()[:120]}
    t = str(f.get("type", "")).strip()[:20]
    q["type"] = t or spec.classify(q["stem"], kind, "")
    return q


def _renumber(p: ExamPaper, qs: list[dict]):
    qs = sorted(qs, key=lambda q: spec.label_key(q["label"]))
    for i, q in enumerate(qs, 1):
        q["n"] = i
    spec._spread_groups(qs)
    p.questions = qs
    p.warnings = spec.validate(qs, len(p.pages or []))


@router.post("/{pid}/q/{n}")
async def save_q(pid: int, n: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    if _busy(db, p):
        raise HTTPException(409, "문항을 읽는 중이에요.")
    qs = [dict(q) for q in p.questions or []]
    i = next((k for k, q in enumerate(qs) if q["n"] == n), None)
    if i is None:
        raise HTTPException(404)
    f = await request.form()
    if f.get("delete") == "1":
        gone = qs.pop(i)
        flash(request, f"{gone['label']}번을 뺐어요.")
    else:
        qs[i] = _q_from_form(f, qs[i])
        flash(request, f"{qs[i]['label']}번을 저장했어요.")
    _renumber(p, qs)
    db.commit()
    return _go(f"/exam/{pid}#q{n}")


@router.post("/{pid}/q")
async def add_q(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    f = await request.form()
    qs = [dict(q) for q in p.questions or []]
    q = _q_from_form(f, {"pages": [], "ai_type": "", "shared_with": ""})
    if any(x["label"] == q["label"] for x in qs):
        flash(request, f"{q['label']}번은 이미 있어요.", "error")
        return _go(f"/exam/{pid}#add")
    qs.append(q)
    _renumber(p, qs)
    db.commit()
    flash(request, f"{q['label']}번을 넣었어요.")
    return _go(f"/exam/{pid}")


@router.post("/{pid}/rotate/{page}")
def rotate_page(pid: int, page: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """쪽 이미지를 시계 방향 90도 돌림 (자동 방향 맞춤이 틀렸을 때). 그 쪽 묶음은 다시 읽도록 지움."""
    p = _own(db, pid, teacher)
    if _busy(db, p) or not (1 <= page <= len(p.pages or [])):
        raise HTTPException(400)
    row = dict(p.pages[page - 1])
    f = db.get(StoredFile, row["file_id"])
    from io import BytesIO

    from PIL import Image
    im = pg.rotate(Image.open(BytesIO(f.data)), 90)
    f.data = pg.to_jpeg(im)
    row.update(rot=(row.get("rot", 0) + 90) % 360, w=im.width, h=im.height, blank=pg.ink_ratio(im) < 0.003)
    pages = list(p.pages)
    pages[page - 1] = row
    p.pages = pages
    chunks = dict(p.chunks or {})
    chunks.pop(str(((page - 1) // spec.CHUNK) * spec.CHUNK), None)
    p.chunks = chunks
    db.commit()
    flash(request, f"{page}쪽을 돌렸어요. '바뀐 쪽 다시 읽기'를 누르면 그 쪽 문항을 새로 읽어요.")
    return _go(f"/exam/{pid}#pages")


@router.post("/{pid}/reread")
async def reread(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """쪽을 골라 다시 읽기 (비우면 지워진 묶음만). 다시 읽으면 고친 내용은 그 쪽 문항만 새로 바뀜 → 전체 문항을 다시 합침."""
    p = _own(db, pid, teacher)
    if _busy(db, p):
        return _go(f"/exam/{pid}")
    if not teacher.gemini_key_enc:
        flash(request, "Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.", "error")
        return _go(f"/exam/{pid}")
    f = await request.form()
    chunks = dict(p.chunks or {})
    for x in f.getlist("page"):
        if str(x).isdigit():
            chunks.pop(str(((int(x) - 1) // spec.CHUNK) * spec.CHUNK), None)
    if f.get("all") == "1":
        chunks = {}
    p.chunks = chunks
    _start(db, p, teacher, p.title)
    return _go(f"/exam/{pid}")


@router.post("/{pid}/pdf")
def make_pdf(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    if not p.questions:
        flash(request, "문항이 없어요.", "error")
        return _go(f"/exam/{pid}")
    job = enqueue(db, academy_id=p.academy_id, teacher_id=teacher.id, kind="exam_pdf", title=f"기출 분석지 · {p.title}", payload={"paper_id": p.id})
    return _go(f"/exam/{pid}?pdf_job={job.id}#out")


@router.post("/{pid}/to_report")
def to_report(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """학생·반 리포트 시험 만들기: 문항 번호·형식·배점·유형·출처를 그대로 넘김 (정답은 선생님이 넣음)."""
    p = _own(db, pid, teacher)
    if not p.questions:
        raise HTTPException(400)
    if p.report_exam_id and db.get(Exam, p.report_exam_id):
        return _go(f"/reports/{p.report_exam_id}?tab=items")
    items = [{"kind": q["kind"], "answer": "", "points": q["points"] or 0, "type": q["type"],
              "source": (q["label"] if not q["label"].isdigit() else "")} for q in p.questions]
    e = Exam(academy_id=p.academy_id, teacher_id=teacher.id, title=p.title, school_id=p.school_id,
             exam_date="", items=rspec.clean_items(items), options={"open": False, "reveal": True, "paper_id": p.id})
    db.add(e)
    db.flush()
    p.report_exam_id = e.id
    db.commit()
    flash(request, "기출 문항으로 리포트용 시험을 만들었어요. 정답 한 줄만 넣고 반을 고르면 바로 채점할 수 있어요.")
    return _go(f"/reports/{e.id}?tab=items")


@router.post("/{pid}/delete")
def remove(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    if _busy(db, p):
        flash(request, "읽는 중에는 지울 수 없어요.", "error")
        return _go(f"/exam/{pid}")
    ids = [r["file_id"] for r in p.pages or []] + list((p.meta or {}).get("upload_ids") or []) + ([p.file_id] if p.file_id else [])
    title = p.title
    p.file_id = None
    db.flush()
    db.delete(p)
    if ids:
        db.execute(delete(StoredFile).where(StoredFile.id.in_(ids)))
    db.commit()
    flash(request, f"'{title}' 기출 분석을 지웠어요.")
    return _go("/exam")
