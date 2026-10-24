"""어법 문제 화면."""
import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..comprehension.spec import make_given
from ..docx_builders import grammar_docx
from ..models import Academy
from ..db import get_db
from ..grammar import spec
from ..jobs import enqueue
from ..models import GrammarSet, Job, StoredFile, Teacher
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/grammar")
LEVELS = ["", "중등", "고1", "고2", "고3"]


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, gid: int, teacher: Teacher) -> GrammarSet:
    gs = db.get(GrammarSet, gid)
    if gs is None or gs.academy_id != teacher.academy_id:
        raise HTTPException(404, "어법 문제를 찾을 수 없습니다.")
    return gs


def _busy(db: Session, gs: GrammarSet) -> bool:
    job = db.get(Job, gs.last_job_id) if gs.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, gs, teacher, kind, title):
    job = enqueue(db, academy_id=gs.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"grammar_id": gs.id})
    gs.last_job_id = job.id
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(GrammarSet).options(selectinload(GrammarSet.teacher)).where(GrammarSet.academy_id == teacher.academy_id).order_by(GrammarSet.id.desc()).limit(100)).scalars())
    return render(request, "grammar/index.html", {"teacher": teacher, "sets": sets, "types": spec.TYPES, "active": "grammar"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    form = {"counts": spec.DEFAULT_COUNTS, "under5": True, "count": True, "show_grammar": True, "passages": [""]}
    return render(request, "grammar/new.html", {"teacher": teacher, "form": form, "spec": spec, "levels": LEVELS, "active": "grammar"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    target = str(f.get("target", "")).strip()[:120]
    title = str(f.get("title", "")).strip()[:120] or (f"{target} 어법 문제" if target else "어법 문제")
    level = str(f.get("level") or "")
    counts = {}
    for t in spec.SENTENCE_TYPES:
        try:
            counts[t] = max(0, min(spec.MAX_PER_TYPE, int(f.get(f"c_{t}") or 0)))
        except ValueError:
            counts[t] = 0
    passages = [str(p).strip() for p in f.getlist("passage") if str(p).strip()][: spec.MAX_PASSAGES]
    options = {"counts": counts, "under5": f.get("under5") == "1", "count": f.get("count") == "1",
               "level": level if level in LEVELS else "", "show_grammar": f.get("show_grammar") == "1"}
    errors = []
    n_passage_q = len(passages) * (options["under5"] + options["count"])
    if sum(counts.values()) + n_passage_q == 0:
        errors.append("만들 문제가 없어요. 유형별 문항 수를 정하거나, 지문을 넣고 지문형 문제를 골라 주세요.")
    if not target and not passages:
        errors.append("목표어법을 쓰거나 지문을 넣어 주세요.")
    if any(len(p) > 4000 for p in passages):
        errors.append("지문은 하나에 4000자까지예요.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    if errors:
        form = {**options, "title": title, "target": target, "passages": passages or [""]}
        return render(request, "grammar/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS, "active": "grammar"}, 400)
    gs = GrammarSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, target=target, options=options, passages=passages)
    db.add(gs)
    db.commit()
    _start(db, gs, teacher, "grammar_build", f"어법 문제 · {title}")
    return _go(f"/grammar/{gs.id}")


@router.get("/{gid}")
def detail(gid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    gs = _own(db, gid, teacher)
    job = db.get(Job, gs.last_job_id) if gs.last_job_id else None
    qs = []
    for q in gs.questions:
        q = dict(q)
        if q["type"] in spec.PASSAGE_TYPES:
            q["pieces"] = spec.place_marks(q["passage"], q["marks"])
        q["answer_text"] = spec.answer_of(q)
        q["problems"] = spec.validate(q)
        qs.append(q)
    return render(request, "grammar/detail.html", {"teacher": teacher, "gs": gs, "job": job, "busy": _busy(db, gs), "qs": qs, "spec": spec, "active": "grammar"})


@router.post("/{gid}/questions")
async def save_questions(gid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    gs = _own(db, gid, teacher)
    if _busy(db, gs):
        flash(request, "만드는 중에는 고칠 수 없어요.", "error")
        return _go(f"/grammar/{gid}")
    f = await request.form()
    g = lambda k, d="": " ".join(str(f.get(k, d)).split())
    out = []
    for i, q in enumerate(copy.deepcopy(gs.questions)):
        p = f"q{i}-"
        if f.get(p + "remove"):
            continue
        q["explain"] = g(p + "explain", q.get("explain", ""))
        t = q["type"]
        if t == "paren":
            q["before"], q["after"] = str(f.get(p + "before", q["before"])), str(f.get(p + "after", q["after"]))
            q["options"] = [g(p + "opt0", q["options"][0]), g(p + "opt1", q["options"][1])]
            pick = f.get(p + "answer")
            q["answer"] = q["options"][int(pick)] if str(pick) in ("0", "1") else q["answer"]
        elif t in ("blank", "pick"):
            if t == "blank":
                q["sentence"] = g(p + "sentence", q["sentence"])
            else:
                q["ask"] = "right" if f.get(p + "ask") == "right" else "wrong"
            q["choices"] = [g(f"{p}c{k}", q["choices"][k] if k < len(q["choices"]) else "") for k in range(5)]
            try:
                q["answer"] = max(1, min(5, int(f.get(p + "answer") or q["answer"])))
            except ValueError:
                pass
        elif t == "arrange":
            old = q["answer"]
            q["ko"], q["answer"], q["condition"] = g(p + "ko", q["ko"]), g(p + "answer", q["answer"]), g(p + "condition", q.get("condition", ""))
            if q["answer"] != old:
                q["given"] = make_given(q["answer"])
        elif t == "transform":
            q["original"], q["instruction"], q["answer"] = g(p + "original", q["original"]), g(p + "instruction", q["instruction"]), g(p + "answer", q["answer"])
        elif f"{p}m0-fix" in f:  # 밑줄 칸이 함께 온 경우에만 (빠진 채 저장돼도 기존 정답 유지)
            for k, m in enumerate(q["marks"]):
                m["wrong"] = f.get(f"{p}m{k}-wrong") == "1"
                m["fix"], m["why"] = g(f"{p}m{k}-fix", m["fix"]), g(f"{p}m{k}-why", m["why"])
        out.append(q)
    gs.questions = out
    gs.options = {**gs.options, "dirty": True}
    db.commit()
    bad = sum(1 for q in out if spec.validate(q))
    flash(request, f"저장했어요 (문항 {len(out)}개)." + (f" 규칙에 안 맞는 문항 {bad}개는 PDF에서 빠집니다." if bad else " 'PDF 다시 만들기'를 눌러 반영하세요."), "error" if bad else "ok")
    return _go(f"/grammar/{gid}")


@router.get("/{gid}/docx")
def docx(gid: int, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    gs = _own(db, gid, teacher)
    if not gs.questions:
        raise HTTPException(404, "아직 문항이 없어요.")
    return grammar_docx(gs, db.get(Academy, gs.academy_id))


@router.post("/{gid}/render")
def rerender(gid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    gs = _own(db, gid, teacher)
    if not _busy(db, gs):
        gs.options = {k: v for k, v in gs.options.items() if k != "dirty"}
        _start(db, gs, teacher, "grammar_render", f"어법 문제 PDF 다시 만들기 · {gs.title}")
    return _go(f"/grammar/{gid}")


@router.post("/{gid}/regenerate")
def regenerate(gid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    gs = _own(db, gid, teacher)
    if not _busy(db, gs):
        if not teacher.gemini_key_enc:
            flash(request, "Gemini API 키가 없습니다.", "error")
            return _go(f"/grammar/{gid}")
        gs.questions, gs.warnings = [], []
        gs.options = {k: v for k, v in gs.options.items() if k != "dirty"}
        _start(db, gs, teacher, "grammar_build", f"어법 문제 새로 만들기 · {gs.title}")
    return _go(f"/grammar/{gid}")


@router.post("/{gid}/delete")
def remove(gid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    gs = _own(db, gid, teacher)
    if _busy(db, gs):
        flash(request, "만드는 중에는 지울 수 없어요.", "error")
        return _go(f"/grammar/{gid}")
    title, fid = gs.title, gs.file_id
    db.delete(gs)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/grammar")
