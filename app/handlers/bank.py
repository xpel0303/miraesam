"""문법뱅크 분류 재배치 작업."""
from ..bank import service
from ..db import session_scope
from ..jobs import JobContext, job_handler


@job_handler("bank_remap")
def remap(ctx: JobContext) -> dict:
    mapping = ctx.payload.get("mapping") or []
    ctx.step(f"{len(mapping):,}개 묶음으로 문항 분류 바꾸는 중", 5)
    with session_scope() as db:
        r = service.remap(db, mapping, step=lambda k, n: ctx.step(f"문항 옮기는 중 ({min(k, n):,}/{n:,})", 10 + int(85 * min(k, n) / max(1, n))))
    return {"reply": f"{r['mapped']:,}문항을 새 분류로 옮겼어요." + (f" 매핑에 없는 {r['unmapped']:,}문항은 '미분류'로 남겼어요." if r["unmapped"] else ""), "model": "분류 재배치"}
