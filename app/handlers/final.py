"""직전대비(본문 숙지 확인 테스트) 작업: 지문마다 AI 한 번 → 검사 → 시험지·정답지 PDF."""
import copy

from sqlalchemy.orm.attributes import flag_modified

from .. import final as spec
from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, FinalSet, StoredFile
from ..pdf import render_pdf


def _save(set_id, idx, data):
    with session_scope() as db:
        s = db.get(FinalSet, set_id)
        ps = copy.deepcopy(s.passages)
        ps[idx].update(data)
        s.passages = ps
        flag_modified(s, "passages")


def render_final_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        s = db.get(FinalSet, set_id)
        academy = db.get(Academy, s.academy_id)
        db.expunge(s)
        db.expunge(academy)
    ctx.step("시험지·정답지 PDF 만드는 중", 92)
    stages = (s.options or {}).get("stages") or list(spec.STAGES)
    items = [{"title": p.get("title") or f"지문 {i + 1}", "d": p["data"], "pts": spec.points(p["data"], stages)}
             for i, p in enumerate(s.passages) if p.get("status") == "done" and p.get("data")]
    base = {"academy": academy, "s": s, "items": items, "stages": stages, "spec": spec}
    test = render_pdf("pdf/final.html", {**base, "doc_title": s.title, "part": "test"})
    key = render_pdf("pdf/final.html", {**base, "doc_title": f"{s.title} · 정답", "part": "key"})
    fid = ctx.save_file(f"{s.title}_본문숙지테스트.pdf", "application/pdf", test)
    kid = ctx.save_file(f"{s.title}_본문숙지테스트_정답.pdf", "application/pdf", key)
    with session_scope() as db:
        row = db.get(FinalSet, set_id)
        old, old_k = row.file_id, (row.options or {}).get("key_file_id")
        row.file_id = fid
        row.options = {**(row.options or {}), "key_file_id": kid}
        db.flush()
        for o in (old, old_k):
            if o and (f := db.get(StoredFile, o)) is not None:
                db.delete(f)
    return {"file_id": fid, "filename": f"{s.title}_본문숙지테스트.pdf", "final_id": set_id,
            "files": [{"file_id": fid, "filename": f"{s.title}_본문숙지테스트.pdf"}, {"file_id": kid, "filename": f"{s.title}_본문숙지테스트_정답.pdf"}]}


@job_handler("final_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["final_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    with session_scope() as db:
        s = db.get(FinalSet, set_id)
        db.expunge(s)
    level = (s.options or {}).get("level", "")
    targets = [i for i in range(len(s.passages)) if (only is None and s.passages[i].get("status") != "done") or (only and i in only)]
    failed = []
    for k, i in enumerate(targets, 1):
        p = s.passages[i]
        ctx.step(f"{p.get('title') or f'지문 {i + 1}'} 문제 만드는 중 ({k}/{len(targets)})", int(5 + 85 * (k - 1) / max(1, len(targets))))
        try:
            best = None
            for _ in range(2):  # 검사 뒤 빠진 문항이 많으면 한 번 더
                raw = llm.generate(key, spec.prompt(p["text"], level), system=spec.SYSTEM, model=model, json_mode=True,
                                   temperature=0.5, max_tokens=8000 + 2 * len(p["text"]))
                d = spec.clean(raw, p["text"], seed=set_id * 100 + i)
                score = len(d["vocab"]) + len(d["ox"]) + len(d["blanks"]) + 2 * len(d["fix"]) + 2 * len(d["arrange"])
                if best is None or score > best[0]:
                    best = (score, d)
                if len(d["vocab"]) >= 8 and len(d["blanks"]) >= 5 and len(d["fix"]) >= 2 and d["ox"]:
                    break
            d = best[1]
            warns = []
            if len(d["sentences"]) < 6:
                warns.append("지문이 짧아서 순서·문장 넣기(5단계)는 빠졌어요.")
            if len(d["vocab"]) < 10 or len(d["blanks"]) < 7 or len(d["fix"]) < 3:
                warns.append("본문과 맞지 않는 문항은 빼서 문항 수가 조금 적어요.")
            _save(set_id, i, {"data": d, "status": "done", "error": None, "warnings": warns})
        except llm.LLMError as e:
            _save(set_id, i, {"status": "failed", "error": str(e)})
            failed.append(f"지문 {i + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    if failed and len(failed) == len(targets):
        raise llm.LLMError(f"{', '.join(failed)} 문제를 만들지 못했습니다. 다시 시도해 주세요.")
    return render_final_pdf(ctx, set_id)


@job_handler("final_render")
def rerender(ctx: JobContext) -> dict:
    return render_final_pdf(ctx, ctx.payload["final_id"])
