"""설명회 자료: 사이트 데이터 모으기 → Gemini 가 슬라이드 구성 → 저장."""
from .. import llm
from ..briefing import spec
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Briefing


@job_handler("briefing")
def make_briefing(ctx: JobContext) -> dict:
    bid = ctx.payload["briefing_id"]
    key, model = ctx.gemini()
    with session_scope() as db:
        b = db.get(Briefing, bid)
        kind, inp, aid = b.kind, dict(b.inputs or {}), b.academy_id
        ctx.step("사이트 데이터 모으는 중", 10)
        fact_text, data = spec.facts(db, aid, kind, inp)
        b.data = data
    ctx.step("AI가 슬라이드를 구성하는 중 (30초~1분)", 30)
    raw = llm.generate(key, spec.prompt(kind, fact_text, data, inp.get("length", "normal"), inp.get("title", "")), system=spec.SYSTEM,
                       model=model, json_mode=True, temperature=0.5, max_tokens=16000)
    title, slides = spec.clean(raw, data)
    if len(slides) < 3:
        raise llm.LLMError("슬라이드를 제대로 받지 못했어요. 다시 만들어 주세요.")
    with session_scope() as db:
        b = db.get(Briefing, bid)
        b.slides = slides
        if title and not inp.get("title"):
            b.title = title
        b.status, b.error = "done", None
    return {"briefing_id": bid, "slides": len(slides)}
