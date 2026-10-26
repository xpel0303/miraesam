"""지문 정리 (지문 한쪽 정리) 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..jobs import enqueue
from ..library.store import remember_form
from ..models import Job, StoredFile, SummarySet, Teacher
from ..summary import spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/summary")
LEVELS = ["", "고1 내신", "고2 내신", "고3 내신", "수능 대비", "중학생"]


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, sid, teacher) -> SummarySet:
    s = db.get(SummarySet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "지문 정리를 찾을 수 없습니다.")
    return s


def _busy(db, s) -> bool:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, s, teacher, kind, title, payload=None):
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"summary_id": s.id, **(payload or {})})
    s.last_job_id = job.id
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(SummarySet).options(selectinload(SummarySet.teacher)).where(SummarySet.academy_id == teacher.academy_id)
                           .order_by(SummarySet.id.desc()).limit(100)).scalars())
    return render(request, "summary/index.html", {"teacher": teacher, "sets": sets, "active": "summary"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "summary/new.html", {"teacher": teacher, "form": {"passages": [{}], "show_lines": True},
                                                "spec": spec, "levels": LEVELS, "active": "summary"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    title = str(f.get("title", "")).strip()[:120] or "지문 정리"
    level = str(f.get("level") or "")
    passages, errors = [], []
    for i, x in enumerate(texts):
        x = str(x).replace("\r", "").strip()
        if not x:
            continue
        if len(x) > 5000:
            errors.append(f"지문 {i + 1}이 너무 깁니다 (5000자까지).")
        passages.append({"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": x, "status": "waiting"})
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    options = {"level": level if level in LEVELS else "", "show_lines": f.get("show_lines") == "1"}
    if errors:
        form = {"title": title, **options, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)] or [{}]}
        return render(request, "summary/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS,
                                                    "active": "summary"}, 400)
    s = SummarySet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "summary_build", f"지문 정리 · {title}")
    remember_form(db, teacher, f)  # 지문 보관함에 저장
    return _go(f"/summary/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return render(request, "summary/detail.html", {"teacher": teacher, "s": s, "job": job, "busy": _busy(db, s), "spec": spec,
                                                   "all_done": all(p.get("status") == "done" for p in s.passages), "active": "summary"})


@router.post("/{sid}/passages/{idx}")
async def save_passage(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages) or not s.passages[idx].get("out"):
        return _go(f"/summary/{sid}")
    f = await request.form()
    g = lambda k: " ".join(str(f.get(k, "")).split())
    ps = copy.deepcopy(s.passages)
    o = ps[idx]["out"]
    for k in ("title", "topic", "purpose", "translation"):
        o[k] = g(k)
    o["summary"] = [x.strip() for x in str(f.get("summary", "")).splitlines() if x.strip()][:3]
    o["vocab"] = spec.parse_vocab(str(f.get("vocab", "")))
    rows = spec.parse_lines(str(f.get("lines", "")))
    if rows:
        o["sentences"] = rows
    bad = [i + 1 for i, r in enumerate(rows) if not r["ok"]]
    ps[idx]["warnings"] = [f"{', '.join(map(str, bad))}번 줄은 영어 덩어리와 우리말 덩어리 개수가 달라요."] if bad else []
    if f.get("p_title") is not None:
        ps[idx]["title"] = str(f.get("p_title")).strip()[:80]
    s.passages = ps
    flag_modified(s, "passages")
    s.options = {**(s.options or {}), "dirty": True}
    db.commit()
    flash(request, "저장했어요. 'PDF 다시 만들기'를 눌러 반영하세요.")
    return _go(f"/summary/{sid}#p{idx}")


@router.post("/{sid}/regenerate/{idx}")
def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/summary/{sid}")
    ps = copy.deepcopy(s.passages)
    ps[idx]["status"] = "waiting"
    s.passages = ps
    flag_modified(s, "passages")
    _start(db, s, teacher, "summary_build", f"지문 {idx + 1} 다시 정리 · {s.title}", {"only": [idx]})
    return _go(f"/summary/{sid}")


@router.post("/{sid}/render")
async def rerender(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        f = await request.form()
        s.options = {**(s.options or {}), "show_lines": f.get("show_lines") == "1"}
        _start(db, s, teacher, "summary_render", f"지문 정리 PDF 다시 만들기 · {s.title}")
    return _go(f"/summary/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "summary_build", f"지문 정리 · {s.title}")
    return _go(f"/summary/{sid}")


@router.post("/{sid}/delete")
def remove(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/summary/{sid}")
    title, fid = s.title, s.file_id
    db.delete(s)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/summary")
