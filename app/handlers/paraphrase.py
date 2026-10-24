"""지문 변형 작업 (지문마다 AI 한 번, 변형도가 강도 범위를 벗어나면 한 번 더)."""
import copy

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, ParaphraseSet, StoredFile
from ..paraphrase import spec
from ..pdf import render_pdf


def _save(set_id, idx, data):
    with session_scope() as db:
        s = db.get(ParaphraseSet, set_id)
        ps = copy.deepcopy(s.passages)
        ps[idx].update(data)
        s.passages = ps
        flag_modified(s, "passages")


def views(s: ParaphraseSet) -> list[dict]:
    out = []
    for i, p in enumerate(s.passages):
        ops, ratio = spec.diff(p["text"], p.get("out", ""))
        out.append({"title": p.get("title") or f"지문 {i + 1}", "p": p, "ops": ops, "ratio": ratio, "changes": spec.change_list(ops),
                    "strength": spec.STRENGTHS[p.get("strength", "medium")]["name"]})
    return out


def render_paraphrase_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        s = db.get(ParaphraseSet, set_id)
        academy = db.get(Academy, s.academy_id)
        db.expunge(s)
        db.expunge(academy)
    ctx.step("PDF 만드는 중", 90)
    pdf = render_pdf("pdf/paraphrase.html", {"academy": academy, "s": s, "views": views(s), "doc_title": s.title,
                                             "show_compare": (s.options or {}).get("show_compare", True)})
    fid = ctx.save_file(f"{s.title}_지문변형.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(ParaphraseSet, set_id)
        old, row.file_id = row.file_id, fid
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": f"{s.title}_지문변형.pdf", "paraphrase_id": set_id}


@job_handler("paraphrase_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["paraphrase_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    with session_scope() as db:
        s = db.get(ParaphraseSet, set_id)
        db.expunge(s)
    o = s.options or {}
    targets = [i for i in range(len(s.passages)) if (only is None and s.passages[i].get("status") != "done") or (only and i in only)]
    failed = []
    for k, i in enumerate(targets, 1):
        p = s.passages[i]
        ctx.step(f"{p.get('title') or f'지문 {i + 1}'} 변형하는 중 ({k}/{len(targets)})", int(5 + 80 * (k - 1) / max(1, len(targets))))
        strength = p.get("strength", "medium")
        fix, best = None, None
        try:
            for _ in range(2):
                raw = llm.generate(key, spec.prompt(p["text"], strength, o.get("level", ""), o.get("focus", ""), fix), system=spec.SYSTEM_PROMPT,
                                   model=model, json_mode=True, temperature=0.7, max_tokens=2500 + len(p["text"]))
                out = (raw.get("text") if isinstance(raw, dict) else "") or ""
                out = " ".join(out.split())
                errs, warns, ratio = spec.check(p["text"], out, strength)
                notes = [str(n).strip() for n in (raw.get("notes") or []) if str(n).strip()][:6] if isinstance(raw, dict) else []
                if out and (best is None or not errs):
                    best = {"out": out, "notes": notes, "ratio": ratio, "warnings": warns + errs}
                if not errs:
                    break
                fix = errs
            if not best:
                raise llm.LLMError("변형 지문을 받지 못했습니다.")
            # 두 번째도 강도 범위를 벗어나면 결과는 살리고 경고로 알림 (선생님이 직접 고칠 수 있음)
            _save(set_id, i, {**best, "status": "done", "error": None})
        except llm.LLMError as e:
            _save(set_id, i, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {i + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 변형하지 못했습니다. 그 지문만 다시 만들 수 있어요.")
    return render_paraphrase_pdf(ctx, set_id)


@job_handler("paraphrase_render")
def rerender(ctx: JobContext) -> dict:
    return render_paraphrase_pdf(ctx, ctx.payload["paraphrase_id"])
