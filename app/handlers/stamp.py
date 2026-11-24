"""스탬프 챌린지 PDF: 카드(A5, 반 학생 이름 채우기 또는 빈 카드) · 학부모 안내문(A4) · 포스터(A3)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import stamp
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import ClassGroup, Student
from ..pdf import render_pdf

KST = ZoneInfo("Asia/Seoul")
NAMES = dict(stamp.DOCS)


@job_handler("stamp_pdf")
def stamp_pdf(ctx: JobContext) -> dict:
    p = ctx.payload
    v = stamp.view(stamp.clean(p.get("settings", {})), datetime.now(KST).date())
    docs = [d for d in p.get("docs", []) if d in NAMES]

    cards = [{"name": "", "cls": ""}] * int(p.get("blank") or 1)
    if "card" in docs and p.get("class_id"):
        with session_scope() as db:
            cg = db.get(ClassGroup, p["class_id"])
            students = db.execute(
                select(Student).options(selectinload(Student.class_group))
                .where(Student.academy_id == ctx.academy_id, Student.class_id == p["class_id"], Student.active.is_(True))
                .order_by(Student.name)
            ).scalars()
            named = [{"name": s.name, "cls": cg.name if cg else ""} for s in students]
        if named:
            cards = named

    files = []
    for i, d in enumerate(docs):
        ctx.step(f"{NAMES[d]} 만드는 중", 10 + 80 * i // len(docs))
        pdf = render_pdf(f"pdf/stamp_{d}.html", {"s": v, "cards": cards}, chunk_key="cards" if d == "card" else None, chunk=30)
        name = f"스탬프 챌린지 {NAMES[d].split(' (')[0]}.pdf"
        files.append({"file_id": ctx.save_file(name, "application/pdf", pdf), "filename": name})
    return {"files": files}
