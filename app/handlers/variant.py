"""변형문제 작업.

variant_build  : 지문마다 순서·삽입은 코드로, 나머지는 AI 한 번 → 규칙 위반 유형만 한 번 더.
                 지문 하나 끝날 때마다 저장(재시작하면 끝난 지문 건너뜀). 실패한 유형은 빼고 경고.
variant_render : 고친 내용으로 PDF만 다시.
"""
import secrets

from .. import llm
from ..db import session_scope
from ..grammar.spec import CIRCLED, LETTERS, place_marks
from ..jobs import JobContext, job_handler
from ..models import Academy, StoredFile, VariantSet
from ..pdf import render_pdf
from ..variant import spec


def _ai(key, model, passage, types, level, notes=None) -> dict:
    raw = llm.generate(key, spec.ai_prompt(passage, types, level, notes), system=spec.SYSTEM_PROMPT, model=model,
                       json_mode=True, temperature=0.5, max_tokens=3000 + 900 * len(types) + len(passage))
    return raw if isinstance(raw, dict) else {}


def _types_for(vs: VariantSet, pi: int) -> list[str]:
    if vs.options.get("mode") == "mix":
        return [a["type"] for a in vs.options.get("plan") or [] if a["p"] == pi]
    return vs.options.get("types", spec.DEFAULT_TYPES)


def _one_passage(key, model, vs: VariantSet, pi: int) -> tuple[list[dict], list[str]]:
    p = vs.passages[pi]
    text, level, types = p["text"], vs.options.get("level", ""), _types_for(vs, pi)
    seed = secrets.randbits(31)
    got: dict[str, dict] = {}
    warns: list[str] = []
    for t in types:
        if t == "order":
            q = spec.make_order(text, seed)
        elif t == "insert":
            q = spec.make_insert(text, seed)
        else:
            continue
        if q and not spec.validate(q):
            got[t] = q
        else:
            warns.append(f"지문 {pi + 1} {spec.TYPES[t]}: 문장이 {spec.MIN_SENTENCES[t]}개 이상이어야 만들 수 있어요.")

    todo = [t for t in types if t in spec.AI_TYPES]
    notes = None
    for _ in range(2):
        if not todo:
            break
        raw = _ai(key, model, text, todo, level, notes)
        still, notes = [], []
        for t in todo:
            q = spec.clean_ai(t, raw.get(t), text, seed)
            errs = spec.validate(q)
            if errs:
                still.append(t)
                notes += errs
            else:
                got[t] = q
        todo = still
    for t in todo:
        warns.append(f"지문 {pi + 1} {spec.TYPES[t]}: 규칙에 맞게 만들지 못해 뺐어요.")
    out = []
    for t in types:  # 선생님이 고른 유형 순서대로
        if t in got:
            got[t]["p"] = pi
            out.append(got[t])
    return out, warns


def render_variant_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        vs = db.get(VariantSet, set_id)
        academy = db.get(Academy, vs.academy_id)
        db.expunge(vs)
        db.expunge(academy)
    qs = [dict(q) for q in vs.questions if not spec.validate(q)]
    if not qs:
        raise llm.LLMError("시험지에 넣을 문항이 없습니다.")
    for q in qs:
        if q["type"] == "vocab":
            q["pieces"] = place_marks(q["passage"], q["marks"])
        q["answer_text"] = spec.answer_text(q)
        q["ptitle"] = vs.passages[q["p"]].get("title", "")
    ctx.step("PDF 만드는 중", 90)
    pdf = render_pdf("pdf/variant.html", {"academy": academy, "vs": vs, "qs": qs, "stems": spec.STEMS, "types": spec.TYPES,
                                          "circled": CIRCLED, "letters": LETTERS, "orders": spec.ORDER_CHOICES, "doc_title": vs.title})
    fid = ctx.save_file(f"{vs.title}_변형문제.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(VariantSet, set_id)
        old, row.file_id = row.file_id, fid
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": f"{vs.title}_변형문제.pdf", "variant_id": set_id}


@job_handler("variant_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["variant_id"]
    key, model = ctx.gemini()
    with session_scope() as db:
        vs = db.get(VariantSet, set_id)
        vs.status, vs.error = "making", None
        db.flush()
        db.expunge(vs)
    done_p = {q["p"] for q in vs.questions}
    questions, warns = list(vs.questions), list(vs.warnings or [])
    try:
        for pi in range(len(vs.passages)):
            if pi in done_p or not _types_for(vs, pi):
                continue  # 재시작으로 이어서 할 때 끝난 지문(섞기에서 문항이 없는 지문)은 건너뜀
            ctx.step(f"지문 {pi + 1}/{len(vs.passages)} 문제 만드는 중", int(5 + 85 * pi / len(vs.passages)))
            qs, w = _one_passage(key, model, vs, pi)
            questions += qs
            warns += w
            with session_scope() as db:
                row = db.get(VariantSet, set_id)
                row.questions, row.warnings = list(questions), list(warns)
        if not questions:
            raise llm.LLMError("문항을 하나도 만들지 못했습니다. " + " ".join(warns[:2]))
        if vs.options.get("mode") == "mix":  # 섞기: 배정표 순서(랜덤)대로 문항 순서를 바꿈
            pos = {(x["p"], x["type"]): k for k, x in enumerate(vs.options.get("plan") or [])}
            questions.sort(key=lambda q: pos.get((q["p"], q["type"]), 999))
            with session_scope() as db:
                db.get(VariantSet, set_id).questions = list(questions)
    except llm.LLMError as e:
        with session_scope() as db:
            row = db.get(VariantSet, set_id)
            row.status, row.error = "failed", str(e)
        raise
    with session_scope() as db:
        db.get(VariantSet, set_id).status = "done"
    return render_variant_pdf(ctx, set_id)


@job_handler("variant_render")
def rerender(ctx: JobContext) -> dict:
    return render_variant_pdf(ctx, ctx.payload["variant_id"])
