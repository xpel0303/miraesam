"""내용이해 워크북 작업.

comprehension_build  : 지문마다 Gemini로 30문항을 만들고, 다 되면 문제지·답지 PDF를 만듭니다.
                       지문 하나가 끝날 때마다 저장하므로, 서버가 재시작돼도 끝난 지문은 건너뜁니다.
comprehension_render : 선생님이 문항을 고친 뒤 PDF만 다시 만듭니다 (Gemini 호출 없음).
"""
import copy

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..comprehension import spec
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, StoredFile, Workbook
from ..pdf import render_pdf


def _load(workbook_id: int) -> Workbook:
    with session_scope() as db:
        wb = db.get(Workbook, workbook_id)
        if wb is None:
            raise RuntimeError("워크북을 찾을 수 없습니다.")
        db.expunge(wb)
        return wb


def _save_passage(workbook_id: int, idx: int, data: dict):
    with session_scope() as db:
        wb = db.get(Workbook, workbook_id)
        passages = copy.deepcopy(wb.passages)
        passages[idx].update(data)
        wb.passages = passages
        flag_modified(wb, "passages")


def _generate_one(ctx: JobContext, key: str, model: str, p: dict) -> dict:
    need_title = not (p.get("title") or "").strip()
    need_ko = not (p.get("passage_ko") or "").strip()
    prompt = spec.user_prompt(p["passage"], target_grammar=p.get("target_grammar", ""), need_title=need_title, need_translation=need_ko)

    errors: list[str] = []
    for attempt in range(2):
        full = prompt if not errors else prompt + "\n\n" + spec.repair_prompt(errors)
        raw = llm.generate(key, full, system=spec.SYSTEM_PROMPT, model=model, json_mode=True, temperature=0.5, max_tokens=14000)
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if not isinstance(raw, dict):
            errors = ["JSON 객체 하나로 답해야 합니다."]
            continue
        result = spec.clean_passage_result(raw)
        merged = {**p, **{k: v for k, v in result.items() if k in spec.SECTION_KEYS}}
        if need_title and result.get("title"):
            merged["title"] = result["title"]
        if need_ko and result.get("passage_ko"):
            merged["passage_ko"] = result["passage_ko"]
        errors, warnings = spec.validate_passage(merged)
        if not errors:
            merged.update(status="done", error=None, warnings=warnings)
            return merged
    raise llm.LLMError("규칙에 맞는 문제를 받지 못했습니다: " + " / ".join(errors[:3]))


def render_workbook_pdfs(ctx: JobContext, workbook_id: int) -> dict:
    wb = _load(workbook_id)
    problems = []
    for i, p in enumerate(wb.passages, 1):
        errs, _ = spec.validate_passage(p, f"지문 {i}")
        problems += errs
    if problems:
        raise llm.LLMError("PDF를 만들 수 없는 문항이 있습니다: " + " / ".join(problems[:4]))

    with session_scope() as db:
        academy = db.get(Academy, wb.academy_id)
        db.expunge(academy)

    opts = {"start_number": 1, "show_grammar": True, "show_translation": True, "split": [6, 4], **(wb.options or {})}
    base = dict(academy=academy, wb=wb, passages=wb.passages, opts=opts, sections=spec.SECTIONS, labels=spec.LABELS, per=spec.PER_PASSAGE)

    ctx.step("문제지 PDF 만드는 중", 85)
    problem_pdf = render_pdf("pdf/comprehension.html", {**base, "answer": False, "doc_title": f"{wb.title} · 문제지"})
    ctx.step("답지 PDF 만드는 중", 93)
    answer_pdf = render_pdf("pdf/comprehension.html", {**base, "answer": True, "doc_title": f"{wb.title} · 답지"})

    pid = ctx.save_file(f"{wb.title}_문제지.pdf", "application/pdf", problem_pdf)
    aid = ctx.save_file(f"{wb.title}_답지.pdf", "application/pdf", answer_pdf)
    with session_scope() as db:
        w = db.get(Workbook, workbook_id)
        old = [i for i in (w.problem_file_id, w.answer_file_id) if i]
        w.problem_file_id, w.answer_file_id = pid, aid
        db.flush()
        for fid in old:  # 예전 PDF는 지워서 DB가 불어나지 않게
            f = db.get(StoredFile, fid)
            if f is not None:
                db.delete(f)
    return {"problem_file_id": pid, "answer_file_id": aid, "workbook_id": workbook_id}


@job_handler("comprehension_build")
def build(ctx: JobContext) -> dict:
    workbook_id = ctx.payload["workbook_id"]
    only = ctx.payload.get("only")  # 지문 하나만 다시 만들 때 [idx]
    key, model = ctx.gemini()
    wb = _load(workbook_id)
    total = len(wb.passages)

    targets = [i for i in range(total) if (only is None or i in only)]
    if only is None:  # 재시작으로 이어서 처리할 때는 끝난 지문을 건너뜀
        targets = [i for i in targets if wb.passages[i].get("status") != "done"]

    failed = []
    for n, idx in enumerate(targets, 1):
        p = wb.passages[idx]
        name = p.get("title") or f"지문 {idx + 1}"
        ctx.step(f"{name} 문제 만드는 중 ({n}/{len(targets)})", int(5 + 75 * (n - 1) / max(1, len(targets))))
        _save_passage(workbook_id, idx, {"status": "making", "error": None})
        try:
            done = _generate_one(ctx, key, model, p)
            _save_passage(workbook_id, idx, done)
        except llm.LLMError as e:
            _save_passage(workbook_id, idx, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {idx + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise  # 다른 지문도 똑같이 실패하므로 바로 멈춤

    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 만들지 못했습니다. 결과 화면에서 그 지문만 다시 만들 수 있어요.")
    return render_workbook_pdfs(ctx, workbook_id)


@job_handler("comprehension_render")
def rerender(ctx: JobContext) -> dict:
    ctx.step("고친 내용 확인하는 중", 50)
    return render_workbook_pdfs(ctx, ctx.payload["workbook_id"])
