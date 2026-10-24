"""지문 분석 작업.

analysis_build  : 지문마다 Gemini로 분석하고, 다 되면 PDF 한 파일을 만듭니다 (끝난 지문은 저장되어 재시작해도 건너뜀).
analysis_render : 고친 내용이나 바꾼 양식으로 PDF만 다시 만듭니다 (Gemini 호출 없음).
"""
import copy

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..analysis import spec
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, AnalysisSet, StoredFile
from ..pdf import render_pdf


def _load(set_id: int) -> AnalysisSet:
    with session_scope() as db:
        s = db.get(AnalysisSet, set_id)
        if s is None:
            raise RuntimeError("분석 자료를 찾을 수 없습니다.")
        db.expunge(s)
        return s


def _save_passage(set_id: int, idx: int, data: dict):
    with session_scope() as db:
        s = db.get(AnalysisSet, set_id)
        passages = copy.deepcopy(s.passages)
        passages[idx].update(data)
        s.passages = passages
        flag_modified(s, "passages")


def _analyze_one(key: str, model: str, p: dict) -> dict:
    sentences = p.get("sentences_src") or spec.split_sentences(p["passage"])
    need_title = not (p.get("title") or "").strip()
    prompt = spec.user_prompt(sentences, need_title=need_title, level=p.get("level", ""))
    # 문장 수에 비례해 답이 길어지므로 출력 한도를 넉넉히
    max_tokens = min(30000, 4000 + 700 * len(sentences))

    errors: list[str] = []
    for _ in range(2):
        full = prompt if not errors else prompt + "\n\n" + spec.repair_prompt(errors)
        raw = llm.generate(key, full, system=spec.SYSTEM_PROMPT, model=model, json_mode=True, temperature=0.4, max_tokens=max_tokens)
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if not isinstance(raw, dict):
            errors = ["JSON 객체 하나로 답해야 합니다."]
            continue
        result, tag_warns = spec.clean_result(raw, sentences)
        errors, warns = spec.validate(result, sentences)
        if not errors:
            out = {**p, **result, "sentences_src": sentences, "status": "done", "error": None, "warnings": tag_warns + warns}
            if not need_title:
                out["title_en"], out["title_ko"] = p.get("title", ""), ""
            elif not out.get("title"):
                out["title"] = result.get("title_en") or result.get("title_ko") or ""
            return out
    raise llm.LLMError("규칙에 맞는 분석을 받지 못했습니다: " + " / ".join(errors[:3]))


def render_analysis_pdf(ctx: JobContext, set_id: int) -> dict:
    s = _load(set_id)
    if not all(p.get("status") == "done" for p in s.passages):
        raise llm.LLMError("아직 분석이 끝나지 않은 지문이 있어 PDF를 만들 수 없습니다.")
    with session_scope() as db:
        academy = db.get(Academy, s.academy_id)
        db.expunge(academy)
    opts = {"layout": "split", "show_background": True, "show_sketch": True, "show_vocab": True, **(s.options or {})}
    ctx.step("PDF 만드는 중", 88)
    pdf = render_pdf(
        "pdf/analysis.html",
        {"academy": academy, "aset": s, "passages": s.passages, "opts": opts, "point_types": spec.POINT_TYPES, "doc_title": s.title},
    )
    fid = ctx.save_file(f"{s.title}_지문분석.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(AnalysisSet, set_id)
        old = row.file_id
        row.file_id = fid
        db.flush()
        if old:
            f = db.get(StoredFile, old)
            if f is not None:
                db.delete(f)
    return {"file_id": fid, "filename": f"{s.title}_지문분석.pdf", "analysis_id": set_id}


@job_handler("analysis_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["analysis_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    s = _load(set_id)
    targets = [i for i in range(len(s.passages)) if only is None or i in only]
    if only is None:
        targets = [i for i in targets if s.passages[i].get("status") != "done"]

    failed = []
    for n, idx in enumerate(targets, 1):
        p = s.passages[idx]
        name = p.get("title") or f"지문 {idx + 1}"
        ctx.step(f"{name} 분석하는 중 ({n}/{len(targets)})", int(5 + 80 * (n - 1) / max(1, len(targets))))
        _save_passage(set_id, idx, {"status": "making", "error": None})
        try:
            _save_passage(set_id, idx, _analyze_one(key, model, p))
        except llm.LLMError as e:
            _save_passage(set_id, idx, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {idx + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 분석하지 못했습니다. 결과 화면에서 그 지문만 다시 만들 수 있어요.")
    return render_analysis_pdf(ctx, set_id)


@job_handler("analysis_render")
def rerender(ctx: JobContext) -> dict:
    return render_analysis_pdf(ctx, ctx.payload["analysis_id"])
