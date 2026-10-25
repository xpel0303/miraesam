"""지문 분석 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..analysis import spec
from ..db import get_db
from ..jobs import enqueue
from ..models import AnalysisSet, Job, StoredFile, Teacher
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/analysis")

MAX_CHARS = 6000
MAX_SENTENCES = 40
LEVELS = ["", "중등 내신", "고1 내신", "고2 내신", "고3 내신", "수능 대비"]
LAYOUTS = {"split": "분석본형 (지문 7 : 주석 3)", "single": "분석자료형 (1단, 포인트는 문장 아래)"}


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, sid: int, teacher: Teacher) -> AnalysisSet:
    s = db.get(AnalysisSet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "분석 자료를 찾을 수 없습니다.")
    return s


def _busy(db: Session, s: AnalysisSet) -> Job | None:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return job if job and job.status in ("queued", "running") else None


def _start(db: Session, s: AnalysisSet, teacher: Teacher, kind: str, payload: dict, title: str) -> Job:
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"analysis_id": s.id, **payload})
    s.last_job_id = job.id
    db.commit()
    return job


def _read_options(f) -> dict:
    layout = str(f.get("layout") or "split")
    return {
        "layout": layout if layout in LAYOUTS else "split",
        "show_background": f.get("show_background") == "1",
        "show_vocab": f.get("show_vocab") == "1",
        "show_visual": f.get("show_visual") == "1",  # 정리 그림(4컷): Gemini 답이 길어지므로 고를 때만
    }


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(
        db.execute(
            select(AnalysisSet).options(selectinload(AnalysisSet.teacher)).where(AnalysisSet.academy_id == teacher.academy_id).order_by(AnalysisSet.id.desc()).limit(100)
        ).scalars()
    )
    return render(request, "analysis/index.html", {"teacher": teacher, "sets": sets, "active": "analysis"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "analysis/new.html", {"teacher": teacher, "form": {}, "levels": LEVELS, "layouts": LAYOUTS, "max": spec.MAX_PASSAGES, "active": "analysis"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts, targets = f.getlist("p_title"), f.getlist("p_text"), f.getlist("p_target")
    level = str(f.get("level") or "")
    level = level if level in LEVELS else ""
    passages, errors = [], []
    for i, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue
        sents = spec.split_sentences(text)
        if len(text) > MAX_CHARS or len(sents) > MAX_SENTENCES:
            errors.append(f"지문 {i + 1}이 너무 깁니다 ({len(sents)}문장). {MAX_SENTENCES}문장 이하로 나눠 주세요.")
        passages.append({"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "passage": text, "level": level, "sentences_src": sents,
                         "target": str(targets[i]).strip()[:80] if i < len(targets) else "", "status": "waiting"})
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    title = str(f.get("title", "")).strip()[:120] or "지문 분석"
    options = _read_options(f)
    if errors:
        form = {"title": title, "level": level, "options": options,
                "passages": [{"title": t, "text": x, "target": targets[k] if k < len(targets) else ""} for k, (t, x) in enumerate(zip(titles, texts))]}
        return render(request, "analysis/new.html", {"teacher": teacher, "form": form, "errors": errors, "levels": LEVELS, "layouts": LAYOUTS, "max": spec.MAX_PASSAGES, "active": "analysis"}, 400)

    s = AnalysisSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "analysis_build", {}, f"지문 분석 · {title}")
    return _go(f"/analysis/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return render(
        request,
        "analysis/detail.html",
        {
            "teacher": teacher, "aset": s, "job": job,
            "busy": job is not None and job.status in ("queued", "running"),
            "all_done": all(p.get("status") == "done" for p in s.passages),
            "point_types": spec.POINT_TYPES, "layouts": LAYOUTS, "icons": spec.ICONS,
            "opts": {**spec.DEFAULT_OPTIONS, **(s.options or {})},
            "legacy": any("points" not in r for p in s.passages for r in (p.get("sentences") or [])),
            "active": "analysis",
        },
    )


@router.post("/{sid}/options")
async def save_options(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        flash(request, "만드는 중에는 바꿀 수 없어요.", "error")
        return _go(f"/analysis/{sid}")
    f = await request.form()
    s.options = {**_read_options(f)}
    title = str(f.get("title", "")).strip()[:120]
    if title:
        s.title = title
    db.commit()
    if all(p.get("status") == "done" for p in s.passages):
        _start(db, s, teacher, "analysis_render", {}, f"지문 분석 PDF 다시 만들기 · {s.title}")
    return _go(f"/analysis/{sid}")


@router.post("/{sid}/passages/{idx}")
async def save_passage(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        flash(request, "만드는 중에는 고칠 수 없어요.", "error")
        return _go(f"/analysis/{sid}")
    if not 0 <= idx < len(s.passages):
        raise HTTPException(404)
    f = await request.form()
    passages = copy.deepcopy(s.passages)
    p = passages[idx]
    p["title_en"] = str(f.get("title_en", p.get("title_en", ""))).strip()
    p["title_ko"] = str(f.get("title_ko", p.get("title_ko", ""))).strip()
    p["target"] = str(f.get("target", p.get("target", ""))).strip()[:80]
    for row in p.get("sentences") or []:
        pre = f"s{row['n']}-"
        row["ko"] = str(f.get(pre + "ko", row.get("ko", ""))).strip()
        pts = []
        for k in range(spec.MAX_POINTS):
            ptype, ptext = str(f.get(f"{pre}pt{k}-type", "")), str(f.get(f"{pre}pt{k}-text", "")).strip()
            if ptype in spec.POINT_TYPES and ptext:
                pts.append({"type": ptype, "text": ptext})
        row["points"] = pts
        row.pop("point", None)
        row["topic"] = f.get(pre + "topic") == "1"
        row["insertion"] = f.get(pre + "ins") == "1"
    summ = p.setdefault("summary", {})
    summ["topic"] = str(f.get("topic", summ.get("topic", ""))).strip()
    summ["background"] = str(f.get("background", summ.get("background", ""))).strip()
    flow = []
    for line in str(f.get("flow", "")).splitlines():
        if line.strip():
            ko, _, desc = line.partition("/")
            flow.append({"ko": ko.strip(), "desc": desc.strip(), "en": ""})
    if flow:
        summ["flow"] = flow[:6]
    vocab = []
    for line in str(f.get("vocab", "")).splitlines():
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0]:
            cells += [""] * (4 - len(cells))
            vocab.append(dict(zip(("word", "meaning", "syn", "ant"), cells[:4])))
    p["vocab"] = vocab[:20]
    if p.get("visual"):
        vis = {"title": str(f.get("vis_title", "")).strip()[:60], "message": str(f.get("vis_message", "")).strip()[:120], "panels": []}
        for k in range(4):
            head = str(f.get(f"vis{k}-head", "")).strip()[:30]
            if head:
                icon = str(f.get(f"vis{k}-icon", "idea"))
                vis["panels"].append({"icon": icon if icon in spec.ICONS else "idea", "head": head, "text": str(f.get(f"vis{k}-text", "")).strip()[:160]})
        p["visual"] = vis if len(vis["panels"]) >= 2 else None
    _, p["warnings"] = spec.validate(p, p.get("sentences_src") or [])
    s.passages = passages
    flag_modified(s, "passages")
    s.options = {**(s.options or {}), "dirty": True}
    db.commit()
    flash(request, "저장했어요. PDF에 반영하려면 'PDF 다시 만들기'를 눌러 주세요.")
    return _go(f"/analysis/{sid}#p{idx}")


@router.post("/{sid}/render")
def rerender(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        s.options = {k: v for k, v in (s.options or {}).items() if k != "dirty"}
        _start(db, s, teacher, "analysis_render", {}, f"지문 분석 PDF 다시 만들기 · {s.title}")
    return _go(f"/analysis/{sid}")


@router.post("/{sid}/regenerate/{idx}")
def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        flash(request, "이미 만드는 중이에요.", "error")
        return _go(f"/analysis/{sid}")
    if not teacher.gemini_key_enc:
        flash(request, "Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.", "error")
        return _go(f"/analysis/{sid}")
    if not 0 <= idx < len(s.passages):
        raise HTTPException(404)
    passages = copy.deepcopy(s.passages)
    passages[idx]["status"] = "waiting"
    s.passages = passages
    flag_modified(s, "passages")
    _start(db, s, teacher, "analysis_build", {"only": [idx]}, f"지문 {idx + 1} 다시 분석 · {s.title}")
    return _go(f"/analysis/{sid}")


@router.post("/{sid}/regenerate")
def regenerate_all(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """모든 지문을 새로 분석 (예전 양식으로 만든 자료를 지금 양식으로 바꿀 때)."""
    s = _own(db, sid, teacher)
    if _busy(db, s):
        flash(request, "이미 만드는 중이에요.", "error")
        return _go(f"/analysis/{sid}")
    if not teacher.gemini_key_enc:
        flash(request, "Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.", "error")
        return _go(f"/analysis/{sid}")
    passages = copy.deepcopy(s.passages)
    for p in passages:
        p["status"] = "waiting"
    s.passages = passages
    flag_modified(s, "passages")
    _start(db, s, teacher, "analysis_build", {}, f"지문 분석 모두 다시 · {s.title}")
    flash(request, f"지문 {len(passages)}개를 새 양식으로 다시 분석하고 있어요. 끝나면 PDF가 새로 만들어져요.")
    return _go(f"/analysis/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "analysis_build", {}, f"지문 분석 · {s.title}")
    return _go(f"/analysis/{sid}")


@router.post("/{sid}/delete")
def delete(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        flash(request, "만드는 중에는 지울 수 없어요.", "error")
        return _go(f"/analysis/{sid}")
    title, fid = s.title, s.file_id
    db.delete(s)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/analysis")
