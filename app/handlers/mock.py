"""동형 모의고사 · 학교 경향분석 작업.

trend_memo  : 학교·학년 기출 여러 회 → 출제 스타일 메모 (Gemini 한 번)
mock_build  : 지문 배정 → 묶음마다 문항 만들기(끝난 묶음은 저장, 재시작하면 이어서) → 정답 번호 고르게 → PDF 2개
mock_unit   : 한 묶음(지문 하나를 같이 쓰는 문항들)만 다시 만들기 → PDF
mock_render : 고친 내용으로 PDF만 다시
"""
import copy

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..mock import spec, trend
from ..models import Academy, ExamPaper, MockSet, School, SchoolTrend, StoredFile
from ..pdf import render_pdf


# ---------------------------------------------------------------- 학교 경향 메모

@job_handler("trend_memo")
def memo(ctx: JobContext) -> dict:
    tid = ctx.payload["trend_id"]
    key, model = ctx.gemini()
    with session_scope() as db:
        t = db.get(SchoolTrend, tid)
        school = db.get(School, t.school_id)
        q = db.query(ExamPaper).filter(ExamPaper.academy_id == t.academy_id, ExamPaper.school_id == t.school_id, ExamPaper.status == "done")
        if t.grade:
            q = q.filter(ExamPaper.grade == t.grade)
        papers = q.order_by(ExamPaper.year, ExamPaper.semester, ExamPaper.term, ExamPaper.id).all()
        for p in papers:
            db.expunge(p)
        name, grade = school.name, t.grade
    if not papers:
        raise llm.LLMError("이 학교의 기출 분석이 아직 없어요. 기출 분석에서 시험지를 먼저 올려 주세요.")
    ctx.step(f"{name} 시험 {len(papers)}회 경향 읽는 중", 30)
    raw = llm.generate(key, trend.memo_prompt(name, grade, papers), system=trend.SYSTEM, model=model, json_mode=True,
                       temperature=0.3, max_tokens=6000, thinking="medium")
    m = trend.clean_memo(raw)
    if not m:
        raise llm.LLMError("경향 메모를 받지 못했어요. 다시 시도해 주세요.")
    with session_scope() as db:
        t = db.get(SchoolTrend, tid)
        t.memo, t.exam_ids = m, [p.id for p in papers]
    return {"trend_id": tid}


# ---------------------------------------------------------------- 동형 모의고사

def _load(mid: int) -> MockSet:
    with session_scope() as db:
        m = db.get(MockSet, mid)
        if m is None:
            raise RuntimeError("동형 모의고사를 찾을 수 없습니다.")
        db.expunge(m)
        return m


def _save(mid: int, **kw):
    with session_scope() as db:
        m = db.get(MockSet, mid)
        for k, v in kw.items():
            setattr(m, k, copy.deepcopy(v))


def _memo_text(m: MockSet) -> str:
    if not m.school_id:
        return ""
    with session_scope() as db:
        q = db.query(SchoolTrend).filter(SchoolTrend.academy_id == m.academy_id, SchoolTrend.school_id == m.school_id)
        rows = q.all()
        g = (m.options or {}).get("grade", "")
        row = next((r for r in rows if r.grade == g), None) or next((r for r in rows if not r.grade), None) or (rows[0] if rows else None)
        return trend.memo_text(row.memo) if row and row.memo else ""


def _match(raw_items, unit: list[dict]) -> list[dict]:
    """Gemini가 돌려준 문항을 설계도 칸에 맞춤 (번호가 같으면 번호로, 아니면 순서로)."""
    raw_items = [x for x in (raw_items or []) if isinstance(x, dict)]
    by_label = {spec._s(x.get("label")): x for x in raw_items}
    out = []
    for i, s in enumerate(unit):
        out.append(by_label.get(s["label"]) or (raw_items[i] if i < len(raw_items) else {}))
    return out


def _make_unit(key, model, m: MockSet, unit: list[dict], memo_text: str, used: list[str]) -> tuple[list[dict], list[str]]:
    """묶음 하나 → 문항들. 규칙 위반이면 한 번 더. 그래도 남은 문제는 경고로 (문항은 남겨서 선생님이 고치게)."""
    level = (m.options or {}).get("grade", "")
    standalone = not unit[0]["needs_passage"]
    p = None if standalone else m.passages[unit[0]["p"]]
    src = "범위 어휘" if standalone else (p.get("source") or p.get("title") or f"지문 {unit[0]['p'] + 1}")
    fix, items, change = None, [], ""
    for _ in range(2):
        prompt = (spec.standalone_prompt(unit, m.passages, used, memo_text, fix, level) if standalone
                  else spec.unit_prompt(unit, p, memo_text, fix, level))
        raw = llm.generate(key, prompt, system=spec.SYSTEM, model=model, json_mode=True, temperature=0.6,
                           max_tokens=4000 + 2500 * len(unit), thinking="medium", timeout=240.0)
        raw = raw if isinstance(raw, dict) else {}
        change = spec._s(raw.get("change"))
        items = [spec.clean_item(r, s, src) for r, s in zip(_match(raw.get("items"), unit), unit)]
        if not standalone and items and not items[0]["passage"]:  # 지문을 엉뚱한 문항에 넣었으면 첫 문항으로
            for it in items[1:]:
                if spec.words(it["passage"]) >= 30:
                    items[0]["passage"], it["passage"] = it["passage"], ""
                    break
        fix = spec.validate_unit(items, unit)
        if not fix:
            break
    for it in items:
        it["change"] = change
    return items, fix or []


def _units_todo(m: MockSet, only: int | None) -> list[list[dict]]:
    done = {it["unit"] for it in m.items}
    us = spec.units(m.slots)
    if only is not None:
        return [u for u in us if u[0]["unit"] == only]
    # 단독 문항은 연속된 것끼리 몇 개씩 한 번에
    out, run = [], []
    for u in us:
        if u[0]["unit"] in done:
            continue
        if not u[0]["needs_passage"]:
            run += u
            if len(run) >= spec.STANDALONE_BATCH:
                out.append(run)
                run = []
            continue
        if run:
            out.append(run)
            run = []
        out.append(u)
    if run:
        out.append(run)
    return out


def _generate(ctx: JobContext, mid: int, only: int | None = None):
    key, model = ctx.gemini()
    m = _load(mid)
    if only is None and any(s["needs_passage"] and s["p"] is None for s in m.slots):
        ctx.step("지문을 문항 묶음에 나누는 중", 3)
        slots = copy.deepcopy(m.slots)
        try:
            raw = llm.generate(key, spec.plan_prompt(slots, m.passages), system="JSON 객체 하나로만 답합니다.", model=model,
                               json_mode=True, temperature=0.2, max_tokens=3000)
        except llm.LLMError:
            raw = None
        if not spec.apply_plan(slots, raw, len(m.passages)):
            spec.default_plan(slots, len(m.passages))
        _save(mid, slots=slots)
        m = _load(mid)
    memo_text = _memo_text(m)
    todo = _units_todo(m, only)
    warns = [w for w in (m.warnings or []) if only is None or not w.startswith(f"[{only}]")]
    items = [it for it in m.items if only is None or it["unit"] != only]
    for i, unit in enumerate(todo):
        labels = ", ".join(s["label"] for s in unit)
        ctx.step(f"{labels} 만드는 중 ({i + 1}/{len(todo)})", int(8 + 80 * i / max(1, len(todo))))
        used = [it["choices"][it["answer"] - 1] for it in items if it["kind"] == "mc" and not it["passage"] and it["answer"] and it["choices"]]
        new, problems = _make_unit(key, model, m, unit, memo_text, used[:40])
        items += new
        warns += [f"[{unit[0]['unit']}] {x}" for x in problems]
        _save(mid, items=spec.order_items(items, m.slots), warnings=warns)
    items = spec.order_items(items, m.slots)
    if only is None:
        spec.balance(items, seed=mid)
    _save(mid, items=items, warnings=warns, status="done", error=None)


def render_mock(ctx: JobContext, mid: int) -> dict:
    m = _load(mid)
    if not m.items:
        raise llm.LLMError("시험지에 넣을 문항이 없습니다.")
    with session_scope() as db:
        academy = db.get(Academy, m.academy_id)
        school = db.get(School, m.school_id) if m.school_id else None
        db.expunge(academy)
        sname = school.name if school else ""
    ctx.step("시험지 PDF 만드는 중", 90)
    pts = [it["points"] for it in m.items if it["points"] is not None]
    ctx_ = {"academy": academy, "m": m, "items": m.items, "c": spec.counts(m.items), "school": sname, "spec": spec,
            "levels": {it["label"]: spec.level(it["points"], pts) for it in m.items}, "doc_title": m.title}
    exam = render_pdf("pdf/mock_exam.html", ctx_)
    ctx.step("정답·해설 PDF 만드는 중", 95)
    key_pdf = render_pdf("pdf/mock_key.html", ctx_)
    fid = ctx.save_file(f"{m.title}_시험지.pdf", "application/pdf", exam)
    kid = ctx.save_file(f"{m.title}_정답해설.pdf", "application/pdf", key_pdf)
    with session_scope() as db:
        row = db.get(MockSet, mid)
        old = [row.file_id, row.key_file_id]
        row.file_id, row.key_file_id = fid, kid
        row.options = {k: v for k, v in (row.options or {}).items() if k != "dirty"}
        db.flush()
        for o in old:
            if o and (f := db.get(StoredFile, o)) is not None:
                db.delete(f)
    return {"file_id": fid, "filename": f"{m.title}_시험지.pdf", "mock_id": mid}


@job_handler("mock_build")
def build(ctx: JobContext) -> dict:
    mid = ctx.payload["mock_id"]
    _save(mid, status="making", error=None)
    try:
        _generate(ctx, mid)
    except llm.LLMError as e:
        _save(mid, status="failed", error=str(e))
        raise
    return render_mock(ctx, mid)


@job_handler("mock_unit")
def remake_unit(ctx: JobContext) -> dict:
    mid, unit = ctx.payload["mock_id"], int(ctx.payload["unit"])
    _generate(ctx, mid, only=unit)
    return render_mock(ctx, mid)


@job_handler("mock_render")
def rerender(ctx: JobContext) -> dict:
    return render_mock(ctx, ctx.payload["mock_id"])
