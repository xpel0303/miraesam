"""직전대비 — 본문 숙지 확인 테스트 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from .. import final as spec
from ..db import get_db
from ..jobs import enqueue
from ..library.store import remember_form
from ..models import FinalSet, Job, StoredFile, Teacher
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/final")


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, sid, teacher) -> FinalSet:
    s = db.get(FinalSet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "직전대비 시험지를 찾을 수 없습니다.")
    return s


def _busy(db, s) -> bool:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, s, teacher, kind, title, payload=None):
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"final_id": s.id, **(payload or {})})
    s.last_job_id = job.id
    db.commit()


def _ctx(teacher, **kw):
    return {"teacher": teacher, "spec": spec, "active": "final", **kw}


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(FinalSet).options(selectinload(FinalSet.teacher)).where(FinalSet.academy_id == teacher.academy_id)
                           .order_by(FinalSet.id.desc()).limit(100)).scalars())
    return render(request, "final/index.html", _ctx(teacher, sets=sets))


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "final/new.html", _ctx(teacher, form={"passages": [{}], "stages": list(spec.STAGES)}))


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    title = str(f.get("title", "")).strip()[:120] or "직전대비 본문 숙지 확인"
    level = str(f.get("level") or "")
    stages = [k for k in spec.STAGES if k in f.getlist("stages")]
    passages, errors = [], []
    for i, x in enumerate(texts):
        x = " ".join(str(x).split())
        if not x:
            continue
        if len(x) > 6000:
            errors.append(f"지문 {i + 1}이 너무 깁니다 (6000자까지).")
        elif len(spec.split_sentences(x)) < 3:
            errors.append(f"지문 {i + 1}이 너무 짧아요 (세 문장 이상).")
        passages.append({"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": x, "status": "waiting"})
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    if not stages:
        errors.append("단계를 하나 이상 골라 주세요.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    options = {"level": level if level in spec.LEVELS else "", "stages": stages}
    if errors:
        form = {"title": title, **options, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)] or [{}]}
        return render(request, "final/new.html", _ctx(teacher, form=form, errors=errors), 400)
    s = FinalSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "final_build", f"직전대비 · {title}")
    remember_form(db, teacher, f)
    return _go(f"/final/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    stages = (s.options or {}).get("stages") or list(spec.STAGES)
    pts = [spec.points(p["data"], stages) if p.get("data") else {} for p in s.passages]
    return render(request, "final/detail.html", _ctx(teacher, s=s, job=job, busy=_busy(db, s), stages=stages, pts=pts,
                                                    any_done=any(p.get("status") == "done" for p in s.passages),
                                                    all_done=all(p.get("status") == "done" for p in s.passages)))


@router.post("/{sid}/stages")
async def set_stages(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """넣을 단계만 바꿔서 PDF 다시 만들기 (AI 다시 안 돌림)."""
    s = _own(db, sid, teacher)
    f = await request.form()
    stages = [k for k in spec.STAGES if k in f.getlist("stages")]
    if not _busy(db, s) and stages:
        s.options = {**(s.options or {}), "stages": stages}
        _start(db, s, teacher, "final_render", f"직전대비 PDF 다시 만들기 · {s.title}")
    elif not stages:
        flash(request, "단계를 하나 이상 골라 주세요.")
    return _go(f"/final/{sid}")


@router.post("/{sid}/regenerate/{idx}")
def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/final/{sid}")
    ps = copy.deepcopy(s.passages)
    ps[idx].update(status="waiting")
    s.passages = ps
    flag_modified(s, "passages")
    _start(db, s, teacher, "final_build", f"지문 {idx + 1} 다시 만들기 · {s.title}", {"only": [idx]})
    return _go(f"/final/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "final_build", f"직전대비 · {s.title}")
    return _go(f"/final/{sid}")


@router.post("/{sid}/delete")
def remove(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/final/{sid}")
    title, fids = s.title, [s.file_id, (s.options or {}).get("key_file_id")]
    db.delete(s)
    db.flush()
    for fid in fids:
        if fid and (sf := db.get(StoredFile, fid)) is not None:
            db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/final")
