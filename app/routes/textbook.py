"""본문 워크북 (10단계) 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..jobs import enqueue
from ..library.store import remember_form
from ..models import Job, StoredFile, Teacher, TextbookSet
from ..textbook import spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/textbook")
LEVELS = ["", "고1", "고2", "고3", "중학생"]


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, sid, teacher) -> TextbookSet:
    s = db.get(TextbookSet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "본문 워크북을 찾을 수 없습니다.")
    return s


def _busy(db, s) -> bool:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, s, teacher, kind, title, payload=None):
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"textbook_id": s.id, **(payload or {})})
    s.last_job_id = job.id
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(TextbookSet).options(selectinload(TextbookSet.teacher)).where(TextbookSet.academy_id == teacher.academy_id)
                           .order_by(TextbookSet.id.desc()).limit(100)).scalars())
    return render(request, "textbook/index.html", {"teacher": teacher, "sets": sets, "active": "textbook"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "textbook/new.html", {"teacher": teacher, "form": {"passages": [{}], "sections": spec.SECTION_KEYS, "show_key": True, "order": "type"},
                                                 "spec": spec, "levels": LEVELS, "active": "textbook"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    title = str(f.get("title", "")).strip()[:120] or "본문 워크북"
    passages, errors = [], []
    for i, x in enumerate(texts):
        x = str(x).replace("\r", "").strip()
        if not x:
            continue
        if len(x) > 8000:
            errors.append(f"문단 {i + 1}이 너무 깁니다 (8000자까지). 둘로 나눠 주세요.")
        passages.append({"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": x, "status": "waiting"})
    if not passages:
        errors.append("본문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"문단은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    sections = [k for k in spec.SECTION_KEYS if k in f.getlist("sections")]
    if not sections:
        errors.append("넣을 워크북을 하나 이상 골라 주세요.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    level = str(f.get("level") or "")
    options = {"lesson": str(f.get("lesson", "")).strip()[:20], "book": str(f.get("book", "")).strip()[:60],
               "level": level if level in LEVELS else "", "sections": sections, "show_key": f.get("show_key") == "1",
               "order": "passage" if f.get("order") == "passage" else "type"}
    if errors:
        form = {"title": title, **options, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)] or [{}]}
        return render(request, "textbook/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS,
                                                     "active": "textbook"}, 400)
    s = TextbookSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "textbook_build", f"본문 워크북 · {title}")
    remember_form(db, teacher, f)
    return _go(f"/textbook/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return render(request, "textbook/detail.html", {"teacher": teacher, "s": s, "job": job, "busy": _busy(db, s), "spec": spec,
                                                    "all_done": all(p.get("status") == "done" for p in s.passages), "active": "textbook"})


@router.post("/{sid}/passages/{idx}")
async def save_passage(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """해석 고치기 (한 줄에 한 문장)."""
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages) or not s.passages[idx].get("rows"):
        return _go(f"/textbook/{sid}")
    f = await request.form()
    ps = copy.deepcopy(s.passages)
    lines = [x.strip() for x in str(f.get("ko", "")).splitlines()]
    for r, ko in zip(ps[idx]["rows"], lines):
        if ko:
            r["ko"] = ko
            r["ko_blanks"] = [b for b in r["ko_blanks"] if b in ko]
    if f.get("p_title") is not None:
        ps[idx]["title"] = str(f.get("p_title")).strip()[:80]
    s.passages = ps
    flag_modified(s, "passages")
    s.options = {**(s.options or {}), "dirty": True}
    db.commit()
    flash(request, "저장했어요. 'PDF 다시 만들기'를 눌러 반영하세요.")
    return _go(f"/textbook/{sid}#p{idx}")


@router.post("/{sid}/regenerate/{idx}")
def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/textbook/{sid}")
    ps = copy.deepcopy(s.passages)
    ps[idx]["status"] = "waiting"
    s.passages = ps
    flag_modified(s, "passages")
    _start(db, s, teacher, "textbook_build", f"문단 {idx + 1} 다시 만들기 · {s.title}", {"only": [idx]})
    return _go(f"/textbook/{sid}")


@router.post("/{sid}/render")
async def rerender(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        f = await request.form()
        sections = [k for k in spec.SECTION_KEYS if k in f.getlist("sections")] or (s.options or {}).get("sections") or spec.SECTION_KEYS
        s.options = {**(s.options or {}), "sections": sections, "show_key": f.get("show_key") == "1",
                     "order": "passage" if f.get("order") == "passage" else "type"}
        _start(db, s, teacher, "textbook_render", f"본문 워크북 PDF 다시 만들기 · {s.title}")
    return _go(f"/textbook/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "textbook_build", f"본문 워크북 · {s.title}")
    return _go(f"/textbook/{sid}")


@router.post("/{sid}/delete")
def remove(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/textbook/{sid}")
    title, fid = s.title, s.file_id
    db.delete(s)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/textbook")
