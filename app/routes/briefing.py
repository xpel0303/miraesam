"""설명회 자료: 만들기(AI) → 슬라이드 고치기 → 발표(화면) · PowerPoint · PDF."""
import asyncio
import copy
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..briefing import spec
from ..config import BASE_DIR
from ..db import get_db
from ..jobs import enqueue
from ..models import Academy, Briefing, ExamPaper, Job, School, SchoolTrend, Teacher
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/briefing")
GRADES = ["중1", "중2", "중3", "고1", "고2", "고3"]


def _own(db: Session, bid: int, teacher: Teacher) -> Briefing:
    b = db.get(Briefing, bid)
    if b is None or b.academy_id != teacher.academy_id:
        raise HTTPException(404, "설명회 자료를 찾을 수 없습니다.")
    return b


def _g(form, k: str, n: int = 4000) -> str:
    return str(form.get(k, "") or "").strip()[:n]


@router.get("")
def index(request: Request, kind: str = "exam", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    items = list(db.execute(select(Briefing).where(Briefing.academy_id == aid).order_by(Briefing.id.desc()).limit(50)).scalars())
    papers = list(db.execute(select(ExamPaper).where(ExamPaper.academy_id == aid, ExamPaper.status == "done")
                             .order_by(ExamPaper.year.desc().nulls_last(), ExamPaper.id.desc())).scalars())
    trends = list(db.execute(select(SchoolTrend).where(SchoolTrend.academy_id == aid)).scalars())
    sids = {p.school_id for p in papers if p.school_id} | {t.school_id for t in trends}
    schools = list(db.execute(select(School).where(School.id.in_(sids or {0})).order_by(School.name)).scalars())
    return render(request, "briefing/index.html", {"teacher": teacher, "active": "briefing", "items": items, "kinds": spec.KINDS,
                                                   "kind": kind if kind in spec.KINDS else "exam", "papers": papers, "schools": schools,
                                                   "trend_sids": {t.school_id for t in trends}, "lengths": spec.LENGTHS, "grades": GRADES,
                                                   "has_key": bool(teacher.gemini_key_enc)})


@router.get("/fonts.zip")
async def fonts(teacher: Teacher = Depends(current_teacher)):
    """PowerPoint 글꼴 (주아 + 나눔스퀘어라운드)."""
    from ..briefing.pptx_build import fonts_zip
    data = await asyncio.to_thread(fonts_zip)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("설명회 글꼴 (주아·나눔스퀘어라운드).zip")})


@router.post("")
async def create(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    form = await request.form()
    kind = _g(form, "kind", 10)
    if kind not in spec.KINDS:
        raise HTTPException(400, "설명회 종류를 골라 주세요.")
    if not teacher.gemini_key_enc:
        flash(request, "설정 화면에서 Gemini 키를 먼저 넣어 주세요. AI가 슬라이드를 구성해요.", "error")
        return RedirectResponse(f"/briefing?kind={kind}", status_code=303)
    inp = {k: _g(form, k, 200) for k in ("title", "length", "audience", "when", "contact", "exam_name", "grade", "school_id", "season", "period", "target")}
    inp["notes"] = _g(form, "notes", 4000)
    inp["paper_ids"] = [int(x) for x in form.getlist("paper_ids") if str(x).isdigit()]
    inp["school_ids"] = [int(x) for x in form.getlist("school_ids") if str(x).isdigit()]
    if kind == "exam" and not inp["school_id"] and not inp["paper_ids"]:
        flash(request, "학교나 기출을 하나 이상 골라 주세요.", "error")
        return RedirectResponse("/briefing?kind=exam", status_code=303)
    inp = {k: v for k, v in inp.items() if v not in ("", [], None)}
    title = inp.get("title") or spec.KINDS[kind][0]
    b = Briefing(academy_id=teacher.academy_id, teacher_id=teacher.id, kind=kind, title=title[:120], inputs=inp, status="making")
    db.add(b)
    db.commit()
    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="briefing", title=f"설명회 자료: {b.title}", payload={"briefing_id": b.id})
    b.last_job_id = job.id
    db.commit()
    return RedirectResponse(f"/briefing/{b.id}", status_code=303)


@router.get("/{bid}")
def detail(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    job = db.get(Job, b.last_job_id) if b.last_job_id else None
    busy = job is not None and job.status in ("queued", "running")
    if job is not None and job.status == "failed" and b.status == "making":
        b.status, b.error = "failed", job.error
        db.commit()
    return render(request, "briefing/detail.html", {"teacher": teacher, "active": "briefing", "b": b, "job": job, "busy": busy,
                                                    "academy": teacher.academy, "geom": spec.chart_geom, "layouts": LAYOUT_NAMES,
                                                    "kind_name": spec.KINDS[b.kind][0], "font_dir": "/static/fonts", "fnum": spec._f})


LAYOUT_NAMES = {"cover": "표지", "section": "장 나눔", "bullets": "글머리 목록", "stats": "숫자 칸", "chart": "그래프", "two": "둘 비교",
                "table": "표", "timeline": "일정", "closing": "마무리"}


@router.post("/{bid}/remake")
def remake(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    b.status, b.error = "making", None
    db.commit()
    job = enqueue(db, academy_id=b.academy_id, teacher_id=teacher.id, kind="briefing", title=f"설명회 자료: {b.title}", payload={"briefing_id": b.id})
    b.last_job_id = job.id
    db.commit()
    return RedirectResponse(f"/briefing/{b.id}", status_code=303)


@router.post("/{bid}/title")
async def set_title(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    form = await request.form()
    b.title = _g(form, "title", 120) or b.title
    db.commit()
    return RedirectResponse(f"/briefing/{b.id}", status_code=303)


def _lines(text: str) -> list[str]:
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


def _cells(line: str, n: int) -> list[str]:
    parts = [x.strip() for x in line.split("|")]
    return (parts + [""] * n)[:n]


def form_text(s: dict) -> dict:
    """슬라이드 → 고치기 칸에 넣을 글."""
    return {
        "bullets": "\n".join(s.get("bullets") or []),
        "items": "\n".join(f"{x.get('value', '')} | {x.get('label', '')} | {x.get('note', '')}".rstrip(" |") for x in s.get("items") or []),
        "chart": "\n".join(f"{x['label']} | {spec._f(x['value'])}" for x in (s.get("chart") or {}).get("items") or []),
        "left": "\n".join((s.get("left") or {}).get("bullets") or []),
        "right": "\n".join((s.get("right") or {}).get("bullets") or []),
        "head": " | ".join(s.get("head") or []),
        "rows": "\n".join(" | ".join(r) for r in s.get("rows") or []),
        "steps": "\n".join(f"{x['when']} | {x['what']}" for x in s.get("steps") or []),
    }


@router.post("/{bid}/slide/{i}")
async def slide_save(bid: int, i: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    slides = copy.deepcopy(b.slides or [])
    if not 0 <= i < len(slides):
        raise HTTPException(404)
    form = await request.form()
    old = slides[i]
    lay = _g(form, "layout", 10) if _g(form, "layout", 10) in spec.LAYOUTS else old.get("layout")
    s = {**spec.blank(lay), **{k: v for k, v in old.items() if k != "layout"}, "layout": lay}
    s["title"], s["sub"], s["notes"] = _g(form, "title", 80), _g(form, "sub", 140), _g(form, "notes", 1200)
    if "foot" in form:
        s["foot"] = _g(form, "foot", 120)
    before = form_text(old)
    changed = lambda k: k in form and _g(form, k).replace("\r", "") != before.get(k, "")  # noqa: E731
    if "bullets" in form:
        s["bullets"] = _lines(_g(form, "bullets"))
    if changed("items"):
        s["items"] = [dict(zip(("value", "label", "note"), _cells(ln, 3))) for ln in _lines(_g(form, "items"))]
        s.pop("data", None)
    if "chart_type" in form and s.get("chart"):
        s["chart"] = {**s["chart"], "type": "donut" if _g(form, "chart_type") == "donut" else "bar"}
    if changed("chart"):
        s["chart"] = {"type": (s.get("chart") or {}).get("type", "bar"), "unit": _g(form, "chart_unit", 6),
                      "items": [{"label": c[0], "value": c[1]} for c in (_cells(ln, 2) for ln in _lines(_g(form, "chart")))]}
        s.pop("data", None)
    elif "chart_unit" in form and s.get("chart"):
        s["chart"]["unit"] = _g(form, "chart_unit", 6)
    for side in ("left", "right"):
        if side in form:
            s[side] = {"head": _g(form, f"{side}_head", 40), "bullets": _lines(_g(form, side))}
    if changed("head") or changed("rows"):
        s["head"] = [x.strip() for x in _g(form, "head").split("|")] if _g(form, "head") else []
        n = max([len(s["head"])] + [len(ln.split("|")) for ln in _lines(_g(form, "rows"))])
        s["rows"] = [_cells(ln, n) for ln in _lines(_g(form, "rows"))]
        s.pop("data", None)
    if "steps" in form:
        s["steps"] = [dict(zip(("when", "what"), _cells(ln, 2))) for ln in _lines(_g(form, "steps"))]
    if s.get("data") and s["data"] in (b.data or {}) and lay in ("stats", "chart", "table"):
        d = b.data[s["data"]]  # 데이터 칸 그대로면 데이터를 다시 씀 (모양만 바꿨을 때)
        if d["kind"] != {"stats": "stats", "chart": "chart", "table": "table"}[lay]:
            s.pop("data", None)
    got = spec.clean_slide(s, b.data or {})
    if got is None:
        flash(request, "이 모양에 필요한 내용이 비었어요. (그래프는 '이름 | 숫자' 줄, 표는 줄마다 칸을 | 로 나눠 주세요)", "error")
        return RedirectResponse(f"/briefing/{b.id}#s{i}", status_code=303)
    if lay in ("cover", "closing"):
        got["foot"] = s.get("foot", "")
    slides[i] = got
    b.slides = slides
    flag_modified(b, "slides")
    db.commit()
    flash(request, f"{i + 1}번 슬라이드를 저장했어요.")
    return RedirectResponse(f"/briefing/{b.id}#s{i}", status_code=303)


@router.post("/{bid}/slide/{i}/{action}")
async def slide_action(bid: int, i: int, action: str, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    slides = copy.deepcopy(b.slides or [])
    form = await request.form()
    to = i
    if action == "add":
        lay = _g(form, "layout", 10)
        slides.insert(i + 1, spec.blank(lay if lay in spec.LAYOUTS else "bullets"))
        to = i + 1
    elif not 0 <= i < len(slides):
        raise HTTPException(404)
    elif action == "up" and i > 0:
        slides[i - 1], slides[i] = slides[i], slides[i - 1]
        to = i - 1
    elif action == "down" and i + 1 < len(slides):
        slides[i + 1], slides[i] = slides[i], slides[i + 1]
        to = i + 1
    elif action == "dup":
        slides.insert(i + 1, copy.deepcopy(slides[i]))
        to = i + 1
    elif action == "delete" and len(slides) > 1:
        slides.pop(i)
        to = max(0, i - 1)
    b.slides = slides
    flag_modified(b, "slides")
    db.commit()
    return RedirectResponse(f"/briefing/{b.id}#s{to}", status_code=303)


@router.post("/{bid}/delete")
def delete(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    db.delete(b)
    db.commit()
    flash(request, "설명회 자료를 지웠어요.")
    return RedirectResponse("/briefing", status_code=303)


@router.get("/{bid}/show")
def show(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, bid, teacher)
    return render(request, "briefing/show.html", {"b": b, "academy": teacher.academy, "geom": spec.chart_geom, "font_dir": "/static/fonts",
                                                  "start": int(request.query_params.get("i", "0") or 0)})


def _fname(b: Briefing, ext: str) -> str:
    safe = "".join(c for c in b.title if c not in '\\/:*?"<>|').strip() or "설명회"
    return f"attachment; filename*=UTF-8''{quote(safe + '.' + ext)}"


@router.get("/{bid}/pptx")
async def pptx(bid: int, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    from ..briefing.pptx_build import build
    b = _own(db, bid, teacher)
    if not b.slides:
        raise HTTPException(400, "아직 슬라이드가 없어요.")
    ac = teacher.academy
    data = await asyncio.to_thread(build, b.title, b.slides, ac.name if ac else "", ac.logo if ac else None)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    headers={"Content-Disposition": _fname(b, "pptx")})


@router.get("/{bid}/pdf")
async def pdf(bid: int, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    from ..pdf import render_pdf
    b = _own(db, bid, teacher)
    if not b.slides:
        raise HTTPException(400, "아직 슬라이드가 없어요.")
    academy = db.get(Academy, b.academy_id)
    data = await asyncio.to_thread(render_pdf, "pdf/briefing.html", {"b": b, "slides": b.slides, "academy": academy, "geom": spec.chart_geom,
                                                                     "static_url": (BASE_DIR / "static").as_uri()})
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": _fname(b, "pdf")})
