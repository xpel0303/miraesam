"""문법뱅크 분류 재배치 작업."""
from ..bank import service
from ..db import session_scope
from ..jobs import JobContext, job_handler


@job_handler("bank_remap")
def remap(ctx: JobContext) -> dict:
    mapping = ctx.payload.get("mapping") or []
    step = lambda k, n: ctx.step(f"문항 옮기는 중 ({min(k, n):,}/{n:,})", 10 + int(85 * min(k, n) / max(1, n)))  # noqa: E731
    with session_scope() as db:
        if ctx.payload.get("rules"):
            ctx.step("교재 학년 단계와 기존 분류로 새 자리 찾는 중", 5)
            r = service.remap_rules(db, step=step)
        else:
            ctx.step(f"{len(mapping):,}개 묶음으로 문항 분류 바꾸는 중", 5)
            r = service.remap(db, mapping, step=step)
    return {"reply": f"{r['mapped']:,}문항을 새 분류로 옮겼어요." + (f" 매핑에 없는 {r['unmapped']:,}문항은 '미분류'로 남겼어요." if r["unmapped"] else ""), "model": "분류 재배치"}


CONCEPT_SYSTEM = """당신은 한국 중고등학생용 영어 문법 교재 저자입니다. 문법 개념 설명 카드를 JSON 하나로만 답합니다.
- 쉬운 우리말, 짧은 개조식. 예문은 자연스럽고 짧은 영어 문장(학생 수준)과 우리말 해석.
- 틀린 문법 설명을 쓰지 않습니다. 확실하지 않은 예외는 넣지 않습니다."""


def concept_prompt(s: dict) -> str:
    return "\n".join([
        f"학년 단계: {s['tier_label']}", f"단원: {s['cat']}", f"문법: {s['label']}", "",
        "이 문법의 개념 설명 카드를 만들어 주세요. 출력 형식:",
        '{"title": "카드 제목 (문법 이름, 짧게)",',
        ' "points": ["핵심 규칙 3~5개. 형태·쓰임·주의점. 한 줄씩, 40자 안팎"],',
        ' "forms": "형태 공식 한 줄 (예: have/has + p.p.) — 없으면 빈 문자열",',
        ' "examples": [{"en": "예문", "ko": "해석", "hl": "예문 속 핵심 부분(그대로 일치)"}, ... 3개],',
        ' "tip": "시험에 자주 나오는 함정 한 줄"}',
    ])


def clean_concept(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    s = lambda v, n: " ".join(str(v or "").split())[:n]  # noqa: E731
    pts = [s(x, 120) for x in raw.get("points") or [] if s(x, 120)][:5]
    exs = []
    for e in raw.get("examples") or []:
        if isinstance(e, dict) and s(e.get("en"), 200):
            en, hl = s(e.get("en"), 200), s(e.get("hl"), 80)
            exs.append({"en": en, "ko": s(e.get("ko"), 200), "hl": hl if hl and hl in en else ""})
    if len(pts) < 2 or not exs:
        return None
    return {"title": s(raw.get("title"), 60), "points": pts, "forms": s(raw.get("forms"), 120), "examples": exs[:3], "tip": s(raw.get("tip"), 160)}


@job_handler("bank_concepts")
def concepts(ctx: JobContext) -> dict:
    from .. import llm
    from ..bank import taxonomy
    from ..models import BankConcept

    subs = taxonomy.load()["subs"]
    todo = [x for x in ctx.payload.get("subs") or [] if x in subs]
    key, model = ctx.gemini()
    made, failed = 0, []
    for n, sid in enumerate(todo, 1):
        s = subs[sid]
        ctx.step(f"'{s['label']}' 개념 설명 만드는 중 ({n}/{len(todo)})", int(5 + 90 * (n - 1) / max(1, len(todo))))
        body = None
        for _ in range(2):
            try:
                body = clean_concept(llm.generate(key, concept_prompt(s), system=CONCEPT_SYSTEM, model=model, json_mode=True, temperature=0.4, max_tokens=1500))
            except llm.LLMError as e:
                if "API 키" in str(e):
                    raise
            if body:
                break
        if not body:
            failed.append(s["label"])
            continue
        if not body["title"]:
            body["title"] = s["label"]
        with session_scope() as db:
            row = db.get(BankConcept, sid) or BankConcept(sub_id=sid)
            row.body = body
            db.add(row)
        made += 1
    return {"reply": f"개념 설명 {made}개를 만들었어요." + (f" 못 만든 것: {', '.join(failed[:5])}" if failed else "") + " 시험지 PDF를 받으면 맨 앞에 들어가요.",
            "model": "개념 설명"}
