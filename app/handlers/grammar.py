"""어법 문제 작업.

grammar_build  : 문장형 문제 한 번 + 지문형 문제는 지문·유형마다 한 번 호출. 규칙 위반이면 한 번 더 받음.
                 일부 유형만 실패하면 나머지로 시험지를 만들고 경고로 알려 줍니다.
grammar_render : 고친 내용으로 PDF만 다시 (Gemini 호출 없음).
"""
from .. import llm
from ..db import session_scope
from ..grammar import spec
from ..jobs import JobContext, job_handler
from ..models import Academy, GrammarSet, StoredFile
from ..pdf import render_pdf


def _ask(key, model, prompt, max_tokens):
    raw = llm.generate(key, prompt, system=spec.SYSTEM_PROMPT, model=model, json_mode=True, temperature=0.5, max_tokens=max_tokens)
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, dict):
        raise llm.LLMError("JSON 객체를 받지 못했습니다.")
    return raw


def _sentence_questions(key, model, gs: GrammarSet) -> tuple[list[dict], list[str]]:
    counts = {k: int(v) for k, v in (gs.options.get("counts") or {}).items() if k in spec.SENTENCE_TYPES and int(v) > 0}
    if not counts:
        return [], []
    prompt = spec.sentence_prompt(gs.target, counts, gs.options.get("level", ""), gs.passages)
    total = sum(counts.values())
    errors: list[str] = []
    best: dict[str, list[dict]] = {}
    for _ in range(2):
        raw = _ask(key, model, prompt + ("\n\n" + spec.repair_prompt(errors) if errors else ""), 3000 + 450 * total)
        items = spec.clean_sentence_items(raw)
        errors = []
        for t, want in counts.items():
            good = [q for q in items[t] if not spec.validate(q)]
            bad = [e for q in items[t] for e in spec.validate(q)]
            if len(good) >= len(best.get(t, [])):
                best[t] = good
            if len(good) < want:
                errors.append(f"{spec.TYPES[t]} {want}개 필요, 규칙에 맞는 것 {len(good)}개" + (f" ({bad[0]})" if bad else ""))
        if not errors:
            break
    out, warns = [], []
    for t in spec.SENTENCE_TYPES:
        want = counts.get(t, 0)
        got = best.get(t, [])[:want]
        out += got
        if want and len(got) < want:
            warns.append(f"{spec.TYPES[t]}: {want}개 중 {len(got)}개만 만들었어요.")
    return out, warns


def _passage_question(key, model, gs: GrammarSet, passage: str, qtype: str) -> dict:
    prompt = spec.passage_prompt(passage, qtype, gs.target, gs.options.get("level", ""))
    errors: list[str] = []
    for _ in range(2):
        raw = _ask(key, model, prompt + ("\n\n" + spec.repair_prompt(errors) if errors else ""), 4000 + len(passage) // 2)
        q = spec.clean_passage_item(raw, qtype)
        errors = spec.validate(q) if q else ["형식이 맞지 않습니다."]
        if not errors:
            return q
    raise llm.LLMError(" / ".join(errors[:2]))


def render_grammar_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        gs = db.get(GrammarSet, set_id)
        academy = db.get(Academy, gs.academy_id)
        db.expunge(gs)
        db.expunge(academy)
    qs = [q for q in gs.questions if not spec.validate(q)]
    if not qs:
        raise llm.LLMError("시험지에 넣을 문항이 없습니다.")
    for q in qs:
        if q["type"] in spec.PASSAGE_TYPES:
            q["pieces"] = spec.place_marks(q["passage"], q["marks"])
        q["answer_text"] = spec.answer_of(q)
    ctx.step("PDF 만드는 중", 90)
    pdf = render_pdf("pdf/grammar.html", {"academy": academy, "gs": gs, "qs": qs, "stems": spec.STEMS, "types": spec.TYPES,
                                          "circled": spec.CIRCLED, "letters": spec.LETTERS, "doc_title": gs.title,
                                          "show_grammar": gs.options.get("show_grammar", True)})
    fid = ctx.save_file(f"{gs.title}_어법문제.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(GrammarSet, set_id)
        old, row.file_id = row.file_id, fid
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": f"{gs.title}_어법문제.pdf", "grammar_id": set_id}


@job_handler("grammar_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["grammar_id"]
    key, model = ctx.gemini()
    with session_scope() as db:
        gs = db.get(GrammarSet, set_id)
        gs.status, gs.error = "making", None
        db.flush()
        db.expunge(gs)

    questions, warns = [], []
    try:
        ctx.step("문장형 문제 만드는 중", 10)
        questions, warns = _sentence_questions(key, model, gs)
        kinds = [t for t in spec.PASSAGE_TYPES if gs.options.get(t)]
        jobs = [(i, p, t) for i, p in enumerate(gs.passages) for t in kinds]
        for n, (i, p, t) in enumerate(jobs, 1):
            ctx.step(f"지문 {i + 1} {spec.TYPES[t]} 만드는 중 ({n}/{len(jobs)})", int(30 + 55 * (n - 1) / max(1, len(jobs))))
            try:
                questions.append(_passage_question(key, model, gs, p, t))
            except llm.LLMError as e:
                if "API 키" in str(e) or "모델을 찾을 수" in str(e) or "한도" in str(e):
                    raise
                warns.append(f"지문 {i + 1} {spec.TYPES[t]}: 만들지 못했어요 ({e}).")
        if not questions:
            raise llm.LLMError("문항을 하나도 만들지 못했습니다. " + " ".join(warns[:2]))
    except llm.LLMError as e:
        with session_scope() as db:
            row = db.get(GrammarSet, set_id)
            row.status, row.error, row.questions, row.warnings = "failed", str(e), questions, warns
        raise
    with session_scope() as db:
        row = db.get(GrammarSet, set_id)
        row.questions, row.warnings, row.status = questions, warns, "done"
    return render_grammar_pdf(ctx, set_id)


@job_handler("grammar_render")
def rerender(ctx: JobContext) -> dict:
    return render_grammar_pdf(ctx, ctx.payload["grammar_id"])
