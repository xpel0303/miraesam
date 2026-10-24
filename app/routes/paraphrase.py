"""지문 변형 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..docx_builders import paraphrase_docx
from ..handlers.paraphrase import views
from ..jobs import enqueue
from ..models import Academy, Job, ParaphraseSet, StoredFile, Teacher
from ..paraphrase import spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/paraphrase")
LEVELS = ["", "고1 내신", "고2 내신", "고3 내신", "수능 대비"]


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, sid, teacher) -> ParaphraseSet:
    s = db.get(ParaphraseSet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "지문 변형을 찾을 수 없습니다.")
    return s


def _busy(db, s) -> bool:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, s, teacher, kind, title, payload=None):
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"paraphrase_id": s.id, **(payload or {})})
    s.last_job_id = job.id
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(ParaphraseSet).options(selectinload(ParaphraseSet.teacher)).where(ParaphraseSet.academy_id == teacher.academy_id).order_by(ParaphraseSet.id.desc()).limit(100)).scalars())
    return render(request, "paraphrase/index.html", {"teacher": teacher, "sets": sets, "active": "paraphrase"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "paraphrase/new.html", {"teacher": teacher, "form": {"passages": [{"strength": "medium"}], "show_compare": True},
                                                   "spec": spec, "levels": LEVELS, "active": "paraphrase"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts, strengths = f.getlist("p_title"), f.getlist("p_text"), f.getlist("p_strength")
    title = str(f.get("title", "")).strip()[:120] or "지문 변형"
    level = str(f.get("level") or "")
    passages, errors = [], []
    for i, x in enumerate(texts):
        x = " ".join(str(x).split())
        if not x:
            continue
        st = str(strengths[i]) if i < len(strengths) else "medium"
        if len(x) > 4000:
            errors.append(f"지문 {i + 1}이 너무 깁니다 (4000자까지).")
        passages.append({"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": x, "strength": st if st in spec.STRENGTHS else "medium", "status": "waiting"})
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    options = {"level": level if level in LEVELS else "", "focus": str(f.get("focus", "")).strip()[:200], "show_compare": f.get("show_compare") == "1"}
    if errors:
        form = {"title": title, **options, "passages": [{"title": t, "text": x, "strength": s} for t, x, s in zip(titles, texts, strengths)]}
        return render(request, "paraphrase/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS, "active": "paraphrase"}, 400)
    s = ParaphraseSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "paraphrase_build", f"지문 변형 · {title}")
    return _go(f"/paraphrase/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return render(request, "paraphrase/detail.html", {"teacher": teacher, "s": s, "job": job, "busy": _busy(db, s), "views": views(s), "spec": spec,
                                                      "all_done": all(p.get("status") == "done" for p in s.passages), "active": "paraphrase"})


@router.post("/{sid}/passages/{idx}")
async def save_passage(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/paraphrase/{sid}")
    f = await request.form()
    ps = copy.deepcopy(s.passages)
    p = ps[idx]
    p["out"] = " ".join(str(f.get("out", p.get("out", ""))).split())
    if f.get("title") is not None:
        p["title"] = str(f.get("title")).strip()[:80]
    errs, warns, ratio = spec.check(p["text"], p["out"], p.get("strength", "medium"))
    p["ratio"], p["warnings"] = ratio, errs + warns
    s.passages = ps
    flag_modified(s, "passages")
    s.options = {**s.options, "dirty": True}
    db.commit()
    flash(request, f"저장했어요. 변형도 {ratio}%. 'PDF 다시 만들기'를 눌러 반영하세요.")
    return _go(f"/paraphrase/{sid}#p{idx}")


@router.post("/{sid}/regenerate/{idx}")
async def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/paraphrase/{sid}")
    f = await request.form()
    ps = copy.deepcopy(s.passages)
    st = str(f.get("strength") or ps[idx].get("strength", "medium"))
    ps[idx].update(status="waiting", strength=st if st in spec.STRENGTHS else "medium")
    s.passages = ps
    flag_modified(s, "passages")
    _start(db, s, teacher, "paraphrase_build", f"지문 {idx + 1} 다시 변형 · {s.title}", {"only": [idx]})
    return _go(f"/paraphrase/{sid}")


@router.post("/{sid}/render")
async def rerender(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        f = await request.form()
        s.options = {**{k: v for k, v in s.options.items() if k != "dirty"}, "show_compare": f.get("show_compare") == "1"}
        _start(db, s, teacher, "paraphrase_render", f"지문 변형 PDF 다시 만들기 · {s.title}")
    return _go(f"/paraphrase/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "paraphrase_build", f"지문 변형 · {s.title}")
    return _go(f"/paraphrase/{sid}")


@router.get("/{sid}/docx")
def docx(sid: int, compare: str = "1", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not all(p.get("status") == "done" for p in s.passages):
        raise HTTPException(404, "아직 다 만들어지지 않았어요.")
    return paraphrase_docx(s, db.get(Academy, s.academy_id), views(s), compare == "1")


@router.post("/{sid}/delete")
def remove(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/paraphrase/{sid}")
    title, fid = s.title, s.file_id
    db.delete(s)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/paraphrase")
