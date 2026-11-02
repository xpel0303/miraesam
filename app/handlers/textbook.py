"""본문 워크북 작업: 지문(문단)마다 12문장씩 AI → 재료 저장 (끝난 문단은 건너뜀) → PDF."""
import copy
import secrets

from sqlalchemy.orm.attributes import flag_modified

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, StoredFile, TextbookSet
from ..pdf import render_pdf
from ..textbook import spec


def _save(set_id, idx, data):
    with session_scope() as db:
        s = db.get(TextbookSet, set_id)
        ps = copy.deepcopy(s.passages)
        ps[idx].update(data)
        s.passages = ps
        flag_modified(s, "passages")


def _one(key, model, p: dict, level: str) -> dict:
    sents = spec.split(p["text"])
    rows, odd, warns = [], [], []
    seed = secrets.randbits(31)
    for start in range(0, len(sents), spec.CHUNK):
        part = [(start + i + 1, x) for i, x in enumerate(sents[start:start + spec.CHUNK])]
        with_odd = start == 0  # 어색한 곳 3개는 문단 앞부분 문장에서
        fix, best = None, None
        for _ in range(2):
            raw = llm.generate(key, spec.prompt(part, with_odd, level, fix), system=spec.SYSTEM_PROMPT, model=model, json_mode=True,
                               temperature=0.4, max_tokens=min(32000, 3000 + 900 * len(part)))
            r, o, errs, w = spec.clean(raw, part, seed + start)
            if best is None or len(errs) < len(best[2]):
                best = (r, o, errs, w)
            if not errs:
                break
            fix = errs
        r, o, errs, w = best
        if any(not x["ko"] for x in r) and len(errs) > len(part) // 2:
            raise llm.LLMError("해석을 받지 못했습니다: " + " / ".join(errs[:3]))
        rows += r
        odd += o if with_odd else []
        warns += w + errs
    if len(odd) < 3 and len(rows) >= 3:
        warns.append(f"어색한 곳 찾기 낱말을 {len(odd)}개만 만들었어요.")
    return {"rows": rows, "odd": odd, "warnings": warns[:8], "status": "done", "error": None}


def _answers(s: TextbookSet, ps: list[dict]) -> dict:
    """섹션별 정답 목록 (번호 순서)."""
    rows = [r for p in ps for r in p["rows"]]
    ans = {k: [] for k in spec.SECTION_KEYS}
    for r in rows:
        ans["s2"].append(" / ".join(r["ko_blanks"]))
        ans["s3"].append(" / ".join(p["t"] for p in spec.marked(r["en"], r["en_blanks"]) if p["hit"]))
        ans["s4"].append(r["ko"])
        ans["s5"].append(" / ".join(p["t"] for p in spec.marked(r["en"], [v["text"] for v in r["verbs"]]) if p["hit"]))
        ans["s6"].append(" / ".join(x["ans"] for x in spec.grammar_line(r["en"], r["grammar"]) if x.get("hit")))
        ans["s8"].append(r["en"])
        ans["s9"].append(r["en"])
    for p in ps:
        ans["s7"].append(" / ".join(f"{o['wrong']} → {o['text']}" for o in p["odd"]))
    return ans


def render_textbook_pdf(ctx: JobContext, set_id: int) -> dict:
    with session_scope() as db:
        s = db.get(TextbookSet, set_id)
        academy = db.get(Academy, s.academy_id)
        db.expunge(s)
        db.expunge(academy)
    ps = [p for p in s.passages if p.get("status") == "done" and p.get("rows")]
    if not ps:
        raise llm.LLMError("PDF에 넣을 지문이 없습니다.")
    k = 0
    for p in ps:  # 문장 번호는 문단을 이어서 1번부터
        for r in p["rows"]:
            k += 1
            r["no"] = k
    # Check(s10) 줄은 번호가 이어지게 미리 만듦
    no, check_ans = 1, []
    for p in ps:
        by_n = {o["n"]: o for o in p["odd"]}
        p["check"] = []
        for r in p["rows"]:
            line, no = spec.check_line(r, by_n.get(r["n"]), no, check_ans)
            p["check"].append(line)
    ans = _answers(s, ps)
    ans["s10"] = check_ans
    ctx.step("PDF 만드는 중", 90)
    o = s.options or {}
    pdf = render_pdf("pdf/textbook.html", {"academy": academy, "s": s, "passages": ps, "doc_title": s.title, "spec": spec,
                                           "sections": [x for x in spec.SECTIONS if x[0] in (o.get("sections") or spec.SECTION_KEYS)],
                                           "answers": ans, "show_key": o.get("show_key", True)})
    fid = ctx.save_file(f"{s.title}_본문워크북.pdf", "application/pdf", pdf)
    with session_scope() as db:
        row = db.get(TextbookSet, set_id)
        old, row.file_id = row.file_id, fid
        row.options = {k: v for k, v in (row.options or {}).items() if k != "dirty"}
        db.flush()
        if old and (f := db.get(StoredFile, old)) is not None:
            db.delete(f)
    return {"file_id": fid, "filename": f"{s.title}_본문워크북.pdf", "textbook_id": set_id}


@job_handler("textbook_build")
def build(ctx: JobContext) -> dict:
    set_id = ctx.payload["textbook_id"]
    only = ctx.payload.get("only")
    key, model = ctx.gemini()
    with session_scope() as db:
        s = db.get(TextbookSet, set_id)
        db.expunge(s)
    level = (s.options or {}).get("level", "")
    targets = [i for i in range(len(s.passages)) if (only is None and s.passages[i].get("status") != "done") or (only and i in only)]
    failed = []
    for k, i in enumerate(targets, 1):
        p = s.passages[i]
        ctx.step(f"{p.get('title') or f'문단 {i + 1}'} 만드는 중 ({k}/{len(targets)})", int(5 + 80 * (k - 1) / max(1, len(targets))))
        _save(set_id, i, {"status": "making", "error": None})
        try:
            _save(set_id, i, _one(key, model, p, level))
        except llm.LLMError as e:
            _save(set_id, i, {"status": "failed", "error": str(e)})
            failed.append(f"문단 {i + 1}")
            if "API 키" in str(e) or "모델을 찾을 수" in str(e):
                raise
    if failed:
        raise llm.LLMError(f"{', '.join(failed)}을(를) 만들지 못했습니다. 그 문단만 다시 만들 수 있어요.")
    return render_textbook_pdf(ctx, set_id)


@job_handler("textbook_render")
def rerender(ctx: JobContext) -> dict:
    return render_textbook_pdf(ctx, ctx.payload["textbook_id"])
