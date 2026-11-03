"""흐름분석 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..jobs import enqueue
from ..library.store import remember_form
from ..models import FlowSet, Job, StoredFile, Teacher
from ..flow import spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/flow")
LEVELS = ["", "고1 내신", "고2 내신", "고3 내신", "수능 대비", "중학생"]


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, sid, teacher) -> FlowSet:
    s = db.get(FlowSet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "흐름분석을 찾을 수 없습니다.")
    return s


def _busy(db, s) -> bool:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, s, teacher, kind, title, payload=None):
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"flow_id": s.id, **(payload or {})})
    s.last_job_id = job.id
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(FlowSet).options(selectinload(FlowSet.teacher)).where(FlowSet.academy_id == teacher.academy_id)
                           .order_by(FlowSet.id.desc()).limit(100)).scalars())
    return render(request, "flow/index.html", {"teacher": teacher, "sets": sets, "active": "flow"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "flow/new.html", {"teacher": teacher, "form": {"passages": [{}]},
                                                "spec": spec, "levels": LEVELS, "active": "flow"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    title = str(f.get("title", "")).strip()[:120] or "흐름분석"
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
    options = {"level": level if level in LEVELS else ""}
    if errors:
        form = {"title": title, **options, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)] or [{}]}
        return render(request, "flow/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS,
                                                    "active": "flow"}, 400)
    s = FlowSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "flow_build", f"흐름분석 · {title}")
    remember_form(db, teacher, f)  # 지문 보관함에 저장
    return _go(f"/flow/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return render(request, "flow/detail.html", {"teacher": teacher, "s": s, "job": job, "busy": _busy(db, s), "spec": spec,
                                                   "all_done": all(p.get("status") == "done" for p in s.passages), "active": "flow"})


@router.post("/{sid}/passages/{idx}")
async def save_passage(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages) or not s.passages[idx].get("out"):
        return _go(f"/flow/{sid}")
    f = await request.form()
    g = lambda k: " ".join(str(f.get(k, "")).split())
    ps = copy.deepcopy(s.passages)
    o = ps[idx]["out"]
    for k in ("title", "gist", "top"):
        o[k] = g(k)
    o["branches"] = spec.parse_branches(str(f.get("branches", ""))) or o["branches"]
    o["result"] = {"head": g("result_head"), "items": [" ".join(x.split()) for x in str(f.get("result_items", "")).splitlines() if x.strip()][:4]}
    comic = spec.parse_comic(str(f.get("comic", "")))
    if comic:
        if [(c["words"], c["scene"]) for c in comic] != [(c["words"], c["scene"]) for c in o["comic"]]:
            ps[idx]["img"] = None  # 단어나 장면이 바뀌면 그림은 다시 그려야 함
            ps[idx]["redraw"] = True
        o["comic"] = comic
    ps[idx]["warnings"] = []
    if f.get("p_title") is not None:
        ps[idx]["title"] = str(f.get("p_title")).strip()[:80]
    s.passages = ps
    flag_modified(s, "passages")
    s.options = {**(s.options or {}), "dirty": True}
    db.commit()
    flash(request, "저장했어요. 만화 단어·장면을 바꿨으면 '만화 다시 그리기', 아니면 'PDF 다시 만들기'를 눌러 반영하세요."
          if ps[idx].get("redraw") else "저장했어요. 'PDF 다시 만들기'를 눌러 반영하세요.")
    return _go(f"/flow/{sid}#p{idx}")


@router.post("/{sid}/regenerate/{idx}")
def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/flow/{sid}")
    ps = copy.deepcopy(s.passages)
    ps[idx]["status"] = "waiting"
    s.passages = ps
    flag_modified(s, "passages")
    _start(db, s, teacher, "flow_build", f"지문 {idx + 1} 다시 정리 · {s.title}", {"only": [idx]})
    return _go(f"/flow/{sid}")


@router.post("/{sid}/render")
async def rerender(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "flow_render", f"흐름분석 PDF 다시 만들기 · {s.title}")
    return _go(f"/flow/{sid}")


@router.post("/{sid}/redraw/{idx}")
def redraw(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages) or s.passages[idx].get("status") != "done":
        return _go(f"/flow/{sid}")
    ps = copy.deepcopy(s.passages)
    ps[idx].update({"img": None, "img_error": None, "redraw": False})
    s.passages = ps
    flag_modified(s, "passages")
    _start(db, s, teacher, "flow_draw", f"지문 {idx + 1} 만화 다시 그리기 · {s.title}", {"only": [idx]})
    return _go(f"/flow/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "flow_build", f"흐름분석 · {s.title}")
    return _go(f"/flow/{sid}")


@router.post("/{sid}/delete")
def remove(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/flow/{sid}")
    title, fid = s.title, s.file_id
    db.delete(s)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/flow")
