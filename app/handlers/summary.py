"""지문 정리 작업 (지문마다 AI 한 번, 규칙 위반이면 한 번 더 → PDF)."""
import copy

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, StoredFile, SummarySet
from ..pdf import render_pdf
from ..summary import spec


def _save(set_id, idx, data):
    with session_scope() as db:
        s = db.get(SummarySet, set_id)
        ps = copy.deepcopy(s.passages)
        ps[idx].update(data)
        s.passages = ps
        flag_modified(s, "passages")


def _one(key, model, p: dict, level: str) -> dict:
    sentences = spec.split(p["text"])
    fix, best = None, None
    for _ in range(2):
        raw = llm.generate(key, spec.prompt(p["text"], sentences, level, fix), system=spec.SYSTEM_PROMPT, model=model, json_mode=True,
                           temperature=0.4, max_tokens=min(32000, 5000 + 450 * len(sentences)))
        out, errs, warns = spec.clean(raw, sentences)
        if best is None or len(errs) < len(best[1]):
            best = (out, errs, warns)
        if not errs:
            break
        fix = errs
    out, errs, warns = best
    if not out["translation"] or not out["sentences"]:
        raise llm.LLMError("정리 내용을 받지 못했습니다: " + " / ".join(errs[:3]))
    return {"out": out, "warnings": warns + [e for e in errs if "문장" not in e][:4], "status": "done", "error": None}


def render_summary_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        s = db.get(SummarySet, set_id)
        academy = db.get(Academy, s.academy_id)
        db.expunge(s)
        db.expunge(academy)
    ps = [{**p, "full": spec.passage_text(p)} for p in s.passages if p.get("status") == "done"]
    if not ps:
        raise llm.LLMError("PDF에 넣을 지문이 없습니다.")
    ctx.step("PDF 만드는 중", 90)
    pdf = render_pdf("pdf/summary.html", {"academy": academy, "s": s, "passages": ps, "doc_title": s.title,
                                          "show_lines": (s.options or {}).get("show_lines", True)}, chunk_key="passages")
    fid = ctx.save_file(f"{s.title}_지문정리.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(SummarySet, set_id)
        old, row.file_id = row.file_id, fid
        row.options = {k: v for k, v in (row.options or {}).items() if k != "dirty"}
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": f"{s.title}_지문정리.pdf", "summary_id": set_id}


@job_handler("summary_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["summary_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    with session_scope() as db:
        s = db.get(SummarySet, set_id)
        db.expunge(s)
    level = (s.options or {}).get("level", "")
    targets = [i for i in range(len(s.passages)) if (only is None and s.passages[i].get("status") != "done") or (only and i in only)]
    failed = []
    for k, i in enumerate(targets, 1):
        p = s.passages[i]
        ctx.step(f"{p.get('title') or f'지문 {i + 1}'} 정리하는 중 ({k}/{len(targets)})", int(5 + 80 * (k - 1) / max(1, len(targets))))
        _save(set_id, i, {"status": "making", "error": None})
        try:
            _save(set_id, i, _one(key, model, p, level))
        except llm.LLMError as e:
            _save(set_id, i, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {i + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 정리하지 못했습니다. 그 지문만 다시 만들 수 있어요.")
    return render_summary_pdf(ctx, set_id)


@job_handler("summary_render")
def rerender(ctx: JobContext) -> dict:
    return render_summary_pdf(ctx, ctx.payload["summary_id"])
