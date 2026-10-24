"""문장 시험지 작업 (지문마다 AI 한 번: 해석 + 빈칸 단어 → 나머지는 코드)."""
import copy

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, SentenceSet, StoredFile
from ..pdf import render_pdf
from ..sentence import spec


def _save(set_id, idx, data):
    with session_scope() as db:
        s = db.get(SentenceSet, set_id)
        ps = copy.deepcopy(s.passages)
        ps[idx].update(data)
        s.passages = ps
        flag_modified(s, "passages")


def sections_for(s: SentenceSet) -> list[dict]:
    o = s.options or {}
    seed = int(o.get("seed") or 1)
    return [{"title": p.get("title") or f"지문 {i + 1}", "sections": spec.build_sections(p, o.get("types", list(spec.TYPES)), o.get("shuffle", True), o.get("hint", "count"), seed + i)}
            for i, p in enumerate(s.passages)]


def render_sentence_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        s = db.get(SentenceSet, set_id)
        academy = db.get(Academy, s.academy_id)
        db.expunge(s)
        db.expunge(academy)
    groups = sections_for(s)
    if not any(g["sections"] for g in groups):
        raise llm.LLMError("시험지에 넣을 문장이 없습니다.")
    ctx.step("PDF 만드는 중", 90)
    pdf = render_pdf("pdf/sentence.html", {"academy": academy, "s": s, "groups": groups, "types": spec.TYPES, "stems": spec.STEMS, "doc_title": s.title})
    fid = ctx.save_file(f"{s.title}_문장시험지.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(SentenceSet, set_id)
        old, row.file_id = row.file_id, fid
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": f"{s.title}_문장시험지.pdf", "sentence_id": set_id}


@job_handler("sentence_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["sentence_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    with session_scope() as db:
        s = db.get(SentenceSet, set_id)
        db.expunge(s)
    level = (s.options or {}).get("level", "")
    targets = [i for i in range(len(s.passages)) if (only is None and s.passages[i].get("status") != "done") or (only and i in only)]
    failed = []
    for k, i in enumerate(targets, 1):
        p = s.passages[i]
        ctx.step(f"{p.get('title') or f'지문 {i + 1}'} 해석·빈칸 만드는 중 ({k}/{len(targets)})", int(5 + 80 * (k - 1) / max(1, len(targets))))
        sents = p["sentences"]
        notes = None
        try:
            for _ in range(2):
                raw = llm.generate(key, spec.prompt(sents, level, notes), system=spec.SYSTEM_PROMPT, model=model, json_mode=True,
                                   temperature=0.3, max_tokens=2000 + 220 * len(sents))
                rows, warns = spec.clean(raw if isinstance(raw, dict) else {}, sents)
                notes = spec.validate(rows, sents)
                if not notes:
                    _save(set_id, i, {"rows": rows, "status": "done", "error": None, "warnings": warns})
                    break
            else:
                raise llm.LLMError("규칙에 맞는 답을 받지 못했습니다: " + " / ".join(notes[:2]))
        except llm.LLMError as e:
            _save(set_id, i, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {i + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 만들지 못했습니다. 그 지문만 다시 만들 수 있어요.")
    return render_sentence_pdf(ctx, set_id)


@job_handler("sentence_render")
def rerender(ctx: JobContext) -> dict:
    return render_sentence_pdf(ctx, ctx.payload["sentence_id"])
