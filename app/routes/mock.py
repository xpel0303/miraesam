"""동형 모의고사 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..jobs import enqueue
from ..library.store import remember_form
from ..mock import spec
from ..models import ExamPaper, Job, MockSet, School, SchoolTrend, StoredFile, Teacher
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/mock")


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, mid, teacher) -> MockSet:
    m = db.get(MockSet, mid)
    if m is None or m.academy_id != teacher.academy_id:
        raise HTTPException(404, "동형 모의고사를 찾을 수 없습니다.")
    return m


def _busy(db, m) -> bool:
    job = db.get(Job, m.last_job_id) if m.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, m, teacher, kind, title, **extra):
    job = enqueue(db, academy_id=m.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"mock_id": m.id, **extra})
    m.last_job_id = job.id
    db.commit()


def _papers(db, teacher) -> list[ExamPaper]:
    return list(db.execute(select(ExamPaper).options(selectinload(ExamPaper.school))
                           .where(ExamPaper.academy_id == teacher.academy_id, ExamPaper.status == "done")
                           .order_by(ExamPaper.school_id, ExamPaper.year.desc(), ExamPaper.semester.desc(), ExamPaper.term.desc())).scalars())


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(MockSet).options(selectinload(MockSet.school), selectinload(MockSet.teacher))
                           .where(MockSet.academy_id == teacher.academy_id).order_by(MockSet.id.desc()).limit(100)).scalars())
    return render(request, "mock/index.html", {"teacher": teacher, "sets": sets, "active": "mock"})


@router.get("/new")
def new(request: Request, template: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    papers = _papers(db, teacher)
    form = {"template_id": template or (papers[0].id if papers else None), "passages": [{}]}
    return render(request, "mock/new.html", {"teacher": teacher, "papers": papers, "form": form, "spec": spec, "active": "mock"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    passages = [{"title": str(titles[i]).strip()[:120] if i < len(titles) else "", "source": str(titles[i]).strip()[:120] if i < len(titles) else "",
                 "text": " ".join(str(x).split())} for i, x in enumerate(texts) if str(x).strip()]
    tid = int(f.get("template_id")) if str(f.get("template_id", "")).isdigit() else 0
    tp = db.get(ExamPaper, tid) if tid else None
    title = str(f.get("title", "")).strip()[:120]
    rng = str(f.get("range", "")).strip()[:400]
    errors = []
    if tp is None or tp.academy_id != teacher.academy_id or tp.status != "done" or not tp.questions:
        errors.append("본뜰 학교 기출을 골라 주세요. (기출 분석에서 시험지를 먼저 올려야 해요)")
    if not passages:
        errors.append("새 시험 범위 지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    for i, p in enumerate(passages, 1):
        if len(p["text"]) > 6000:
            errors.append(f"지문 {i}이 너무 깁니다 (6000자까지).")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    if errors:
        form = {"template_id": tid, "title": title, "range": rng, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)] or [{}]}
        return render(request, "mock/new.html", {"teacher": teacher, "papers": _papers(db, teacher), "form": form, "errors": errors,
                                                 "spec": spec, "active": "mock"}, 400)
    slots = spec.blueprint(tp.questions)
    school = tp.school.name if tp.school else ""
    if not title:
        title = f"{school} {tp.grade} 동형 모의고사".strip()
    m = MockSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, school_id=tp.school_id, template_id=tp.id,
                options={"range": rng, "grade": tp.grade, "template_name": tp.title}, passages=passages, slots=slots)
    db.add(m)
    db.commit()
    _start(db, m, teacher, "mock_build", f"동형 모의고사 · {title}")
    remember_form(db, teacher, f)
    return _go(f"/mock/{m.id}")


@router.get("/{mid}")
def detail(mid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    m = _own(db, mid, teacher)
    job = db.get(Job, m.last_job_id) if m.last_job_id else None
    units = []
    by_unit: dict[int, list] = {}
    for it in m.items:
        by_unit.setdefault(it["unit"], []).append(it)
    for u in spec.units(m.slots):
        k = u[0]["unit"]
        its = by_unit.get(k, [])
        has_p = any(it["passage"] for it in its) or not u[0]["needs_passage"]
        units.append({"unit": k, "slots": u, "its": its, "p": u[0]["p"], "needs_passage": u[0]["needs_passage"],
                      "problems": sum((spec.validate(it, has_p) for it in its), []) if its else []})
    c = spec.counts(m.items) if m.items else None
    trend_row = None
    if m.school_id:
        trend_row = db.execute(select(SchoolTrend).where(SchoolTrend.academy_id == m.academy_id, SchoolTrend.school_id == m.school_id)).scalars().first()
    return render(request, "mock/detail.html", {"teacher": teacher, "m": m, "job": job, "busy": _busy(db, m), "units": units, "c": c,
                                                "spec": spec, "trend": trend_row, "active": "mock"})


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except ValueError:
        return default


@router.post("/{mid}/unit/{unit}")
async def save_unit(mid: int, unit: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """묶음 하나 고쳐 저장 (지문·발문·보기·정답·해설)."""
    m = _own(db, mid, teacher)
    if _busy(db, m):
        flash(request, "만드는 중에는 고칠 수 없어요.", "error")
        return _go(f"/mock/{mid}")
    f = await request.form()
    items = copy.deepcopy(m.items)
    for i, it in enumerate(items):
        if it["unit"] != unit:
            continue
        p = f"i{i}-"
        if p + "stem" not in f:
            continue
        for k in ("stem", "passage", "box", "conditions", "answer_text", "grading", "explain"):
            if p + k in f:
                it[k] = str(f.get(p + k, "")).replace("\r\n", "\n").strip()
        if it["kind"] == "mc":
            it["choices"] = [" ".join(str(f.get(f"{p}c{k}", "")).split()) for k in range(5)]
            a = _int(f.get(p + "answer"), it["answer"])
            it["answer"] = a if 1 <= a <= 5 else it["answer"]
    m.items = items
    m.options = {**(m.options or {}), "dirty": True}
    flag_modified(m, "items")
    db.commit()
    flash(request, "저장했어요. PDF에 반영하려면 'PDF 다시 만들기'를 눌러 주세요.")
    return _go(f"/mock/{mid}#u{unit}")


@router.post("/{mid}/unit/{unit}/remake")
async def remake(mid: int, unit: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """묶음 하나만 새로 만들기 (지문을 바꿔서도)."""
    m = _own(db, mid, teacher)
    if _busy(db, m):
        return _go(f"/mock/{mid}")
    f = await request.form()
    slots = copy.deepcopy(m.slots)
    pidx = f.get("p")
    if pidx is not None and str(pidx).isdigit() and int(pidx) < len(m.passages):
        for s in slots:
            if s["unit"] == unit and s["needs_passage"]:
                s["p"] = int(pidx)
    m.slots = slots
    flag_modified(m, "slots")
    _start(db, m, teacher, "mock_unit", f"동형 모의고사 문항 다시 · {m.title}", unit=unit)
    return _go(f"/mock/{mid}#u{unit}")


@router.post("/{mid}/render")
def rerender(mid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    m = _own(db, mid, teacher)
    if not _busy(db, m):
        _start(db, m, teacher, "mock_render", f"동형 모의고사 PDF · {m.title}")
    return _go(f"/mock/{mid}")


@router.post("/{mid}/retry")
def retry(mid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    m = _own(db, mid, teacher)
    if not _busy(db, m):
        _start(db, m, teacher, "mock_build", f"동형 모의고사 · {m.title}")
    return _go(f"/mock/{mid}")


@router.post("/{mid}/info")
async def save_info(mid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    m = _own(db, mid, teacher)
    f = await request.form()
    m.title = str(f.get("title", "")).strip()[:120] or m.title
    m.options = {**(m.options or {}), "range": str(f.get("range", "")).strip()[:400], "dirty": True}
    db.commit()
    flash(request, "저장했어요. PDF에 반영하려면 'PDF 다시 만들기'를 눌러 주세요.")
    return _go(f"/mock/{mid}")


@router.post("/{mid}/delete")
def remove(mid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    m = _own(db, mid, teacher)
    if _busy(db, m):
        flash(request, "만드는 중에는 지울 수 없어요.", "error")
        return _go(f"/mock/{mid}")
    title, fids = m.title, [m.file_id, m.key_file_id]
    db.delete(m)
    db.flush()
    for fid in fids:
        if fid and (sf := db.get(StoredFile, fid)) is not None:
            db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/mock")
