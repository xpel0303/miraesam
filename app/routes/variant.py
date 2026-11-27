"""변형문제 화면."""
import copy
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..analysis.spec import split_sentences
from ..db import get_db
from ..library.store import remember_form
from ..docx_builders import variant_docx
from ..grammar.spec import CIRCLED, place_marks
from ..jobs import enqueue
from ..models import Academy, Job, StoredFile, Teacher, VariantSet
from ..variant import spec
from ..web import merge_grammar, current_teacher, flash, render

router = APIRouter(prefix="/variant")
LEVELS = ["", "고1 내신", "고2 내신", "고3 내신", "수능 대비"]


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, vid, teacher) -> VariantSet:
    vs = db.get(VariantSet, vid)
    if vs is None or vs.academy_id != teacher.academy_id:
        raise HTTPException(404, "변형문제를 찾을 수 없습니다.")
    return vs


def _busy(db, vs) -> bool:
    job = db.get(Job, vs.last_job_id) if vs.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, vs, teacher, kind, title):
    job = enqueue(db, academy_id=vs.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"variant_id": vs.id})
    vs.last_job_id = job.id
    db.commit()


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(VariantSet).options(selectinload(VariantSet.teacher)).where(VariantSet.academy_id == teacher.academy_id).order_by(VariantSet.id.desc()).limit(100)).scalars())
    return render(request, "variant/index.html", {"teacher": teacher, "sets": sets, "active": "variant"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    form = {"types": spec.DEFAULT_TYPES, "mode": "each", "counts": {}, "passages": [{}]}
    return render(request, "variant/new.html", {"teacher": teacher, "form": form, "spec": spec, "levels": LEVELS, "active": "variant"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts, grams = f.getlist("p_title"), f.getlist("p_text"), f.getlist("p_grammar")
    types = [t for t in spec.TYPES if t in f.getlist("types") and t not in spec.LEGACY_TYPES]
    counts = {}
    for t in types:
        try:
            counts[t] = max(1, min(spec.MAX_PER_TYPE, int(str(f.get(f"cnt_{t}") or 1))))
        except ValueError:
            counts[t] = 1
    level = str(f.get("level") or "")
    title = str(f.get("title", "")).strip()[:120] or "변형문제"
    passages = [{"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": " ".join(str(x).split()),
                 "grammar": merge_grammar(grams[i] if i < len(grams) else "", f.get("common_grammar"))}
                for i, x in enumerate(texts) if str(x).strip()]
    errors = []
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    if not types:
        errors.append("만들 유형을 하나 이상 골라 주세요.")
    for i, p in enumerate(passages, 1):
        n = len(split_sentences(p["text"]))
        if len(p["text"]) > 6000:
            errors.append(f"지문 {i}이 너무 깁니다 (6000자까지).")
        short = [spec.TYPES[t] for t in ("order", "insert", "irrelevant") if t in types and n < spec.MIN_SENTENCES[t]]
    mode = "mix" if f.get("mode") == "mix" else "each"
    if passages and types:
        plan = spec.plan_counts(passages, counts, secrets.randbits(31), mode == "mix")
        if not plan:
            errors.append("지문이 짧아서 고른 유형으로 만들 수 있는 문항이 없어요.")
        if len(plan) > spec.MAX_MIX:
            errors.append(f"한 번에 {spec.MAX_MIX}문항까지 만들 수 있어요 (지금 {len(plan)}문항).")
        skipped = [spec.TYPES[t] for t in types if not any(a["type"] == t for a in plan)]
        if skipped and plan:
            errors.append(f"지문이 짧아서 {', '.join(skipped)} 문제를 만들 수 있는 지문이 없어요. 그 유형을 빼 주세요.")
    if any(t in spec.AI_TYPES for t in types) and not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 넣거나, 순서 배열·문장 삽입만 골라 주세요.")
    if errors:
        form = {"title": title, "level": level, "common_grammar": f.get("common_grammar", ""), "types": types, "mode": mode, "counts": counts, "passages": [{"title": t, "text": x, "grammar": grams[i] if i < len(grams) else ""} for i, (t, x) in enumerate(zip(titles, texts))]}
        return render(request, "variant/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS, "active": "variant"}, 400)
    vs = VariantSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, passages=passages,
                    options={"types": types, "level": level if level in LEVELS else "", "mode": "mix", "counts": counts,
                             "shuffle": mode == "mix", "plan": plan})
    db.add(vs)
    db.commit()
    _start(db, vs, teacher, "variant_build", f"변형문제 · {title}")
    remember_form(db, teacher, f)  # 지문 보관함에 저장
    return _go(f"/variant/{vs.id}")


@router.get("/{vid}")
def detail(vid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    vs = _own(db, vid, teacher)
    job = db.get(Job, vs.last_job_id) if vs.last_job_id else None
    qs = []
    for q in vs.questions:
        q = dict(q)
        if q["type"] == "vocab":
            q["pieces"] = place_marks(q["passage"], q["marks"])
        q["answer_label"] = spec.answer_text(q)
        q["view"] = spec.view(q, vs.passages[q["p"]]["text"])
        q["problems"] = spec.validate(q)
        qs.append(q)
    from .reports import online_exams
    return render(request, "variant/detail.html", {"online": online_exams(db, teacher.academy_id, "variant", vs.id), "teacher": teacher, "vs": vs, "job": job, "busy": _busy(db, vs), "qs": qs,
                                                   "spec": spec, "circled": CIRCLED, "active": "variant"})


@router.post("/{vid}/questions")
async def save(vid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    vs = _own(db, vid, teacher)
    if _busy(db, vs):
        flash(request, "만드는 중에는 고칠 수 없어요.", "error")
        return _go(f"/variant/{vid}")
    f = await request.form()
    g = lambda k, d="": " ".join(str(f.get(k, d)).split())
    out = []
    for i, q in enumerate(copy.deepcopy(vs.questions)):
        p = f"q{i}-"
        if f.get(p + "remove"):
            continue
        q["explain"] = g(p + "explain", q.get("explain", ""))
        t = q["type"]
        old = list(q.get("choices") or []) + [""] * 5
        if t in spec.CHOICE_TYPES and f"{p}c0" in f:
            q["choices"] = [g(f"{p}c{k}", old[k]) for k in range(5)]
        if t == "def_pick" and p + "definition" in f:
            q["definition"] = g(p + "definition", q.get("definition", ""))
            text = vs.passages[q["p"]]["text"]
            q["found"] = all(c and re.search(r"(?<![A-Za-z])" + re.escape(c) + r"(?![A-Za-z])", text, re.I) for c in q["choices"])
        if t in ("summary", "link2") and f"{p}a0" in f:
            if t == "summary":
                q["summary"] = g(p + "summary", q["summary"])
            oldp = [c if isinstance(c, list) and len(c) > 1 else ["", ""] for c in old[:5]]
            q["choices"] = [[g(f"{p}a{k}", oldp[k][0]), g(f"{p}b{k}", oldp[k][1])] for k in range(5)]
        if t in ("refer", "gram_explain") and f"{p}m0-note" in f:
            for k, m in enumerate(q["marks"]):
                m["wrong"] = f.get(f"{p}m{k}-wrong") == "1"
                m["note"] = g(f"{p}m{k}-note", m.get("note", ""))
        if (t in spec.CHOICE_TYPES or t in ("summary", "link2")) and str(f.get(p + "answer", "")).isdigit():
            q["answer"] = max(1, min(5, int(f.get(p + "answer"))))
        if t == "match" and f.get(p + "ask") in ("wrong", "right"):
            q["ask"] = f.get(p + "ask")
        if t in ("grammar", "sa_grammar", "definition") and f"{p}m0-fix" in f:
            for k, m in enumerate(q["marks"]):
                m["wrong"] = f.get(f"{p}m{k}-wrong") == "1"
                m["fix"] = g(f"{p}m{k}-fix", m.get("fix", ""))
                if t == "definition":
                    m["definition"] = g(f"{p}m{k}-def", m.get("definition", ""))
                else:
                    m["why"] = g(f"{p}m{k}-why", m.get("why", ""))
        if t == "sa_arrange" and p + "korean" in f:
            q["korean"] = g(p + "korean", q.get("korean", ""))
        if t == "sa_summary" and p + "summary" in f:
            q["summary"] = g(p + "summary", q["summary"])
            q["answers"] = [g(p + "ans0"), g(p + "ans1")]
            text = vs.passages[q["p"]]["text"]
            q["found"] = all(a and re.search(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", text, re.I) for a in q["answers"])
        if t == "vocab" and f"{p}m0-fix" in f:
            for k, m in enumerate(q["marks"]):
                m["wrong"] = f.get(f"{p}m{k}-wrong") == "1"
                m["fix"], m["why"] = g(f"{p}m{k}-fix", m["fix"]), g(f"{p}m{k}-why", m["why"])
        out.append(q)
    vs.questions = out
    vs.options = {**vs.options, "dirty": True}
    db.commit()
    bad = sum(1 for q in out if spec.validate(q))
    flash(request, f"저장했어요 (문항 {len(out)}개)." + (f" 규칙에 안 맞는 문항 {bad}개는 PDF에서 빠집니다." if bad else " 'PDF 다시 만들기'를 눌러 반영하세요."), "error" if bad else "ok")
    return _go(f"/variant/{vid}")


@router.get("/{vid}/docx")
def docx(vid: int, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    vs = _own(db, vid, teacher)
    if not vs.questions:
        raise HTTPException(404, "아직 문항이 없어요.")
    return variant_docx(vs, db.get(Academy, vs.academy_id))


@router.post("/{vid}/render")
def rerender(vid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    vs = _own(db, vid, teacher)
    if not _busy(db, vs):
        vs.options = {k: v for k, v in vs.options.items() if k != "dirty"}
        _start(db, vs, teacher, "variant_render", f"변형문제 PDF 다시 만들기 · {vs.title}")
    return _go(f"/variant/{vid}")


@router.post("/{vid}/regenerate")
def regenerate(vid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    vs = _own(db, vid, teacher)
    if not _busy(db, vs):
        vs.questions, vs.warnings = [], []
        vs.options = {k: v for k, v in vs.options.items() if k != "dirty"}
        if vs.options.get("counts"):  # 유형별 문항 수로 만든 것: 배정도 새로
            vs.options = {**vs.options, "plan": spec.plan_counts(vs.passages, vs.options["counts"], secrets.randbits(31), vs.options.get("shuffle", False))}
        elif vs.options.get("mode") == "mix":  # 섞기는 새로 만들 때 배정도 새로 (다른 조합)
            vs.options = {**vs.options, "plan": spec.plan_mix(vs.passages, vs.options["types"], vs.options.get("mix_n", len(vs.passages)), secrets.randbits(31))}
        _start(db, vs, teacher, "variant_build", f"변형문제 새로 만들기 · {vs.title}")
    return _go(f"/variant/{vid}")


@router.post("/{vid}/delete")
def remove(vid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    vs = _own(db, vid, teacher)
    if _busy(db, vs):
        flash(request, "만드는 중에는 지울 수 없어요.", "error")
        return _go(f"/variant/{vid}")
    title, fid = vs.title, vs.file_id
    db.delete(vs)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/variant")
