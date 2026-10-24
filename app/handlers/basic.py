"""0단계 기본 작업: Gemini 연결 확인, 학생 명단 PDF."""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import llm
from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, ClassGroup, Student
from ..pdf import render_pdf

KST = ZoneInfo("Asia/Seoul")


@job_handler("gemini_test")
def gemini_test(ctx: JobContext) -> dict:
    key, model = ctx.gemini()
    ctx.step(f"{model} 에 연결하는 중", 30)
    answer = llm.generate(
        key,
        '영어 학원 선생님에게 하는 짧은 인사 한 문장을 한국어로 써서 {"reply": "..."} 형식으로 답하세요.',
        model=model,
        json_mode=True,
        max_tokens=512,
        retries=1,
    )
    reply = answer.get("reply") if isinstance(answer, dict) else str(answer)
    return {"model": model, "reply": reply}


@job_handler("roster_pdf")
def roster_pdf(ctx: JobContext) -> dict:
    class_id = ctx.payload.get("class_id")
    ctx.step("명단을 모으는 중", 20)
    with session_scope() as db:
        academy = db.get(Academy, ctx.academy_id)
        q = (
            select(Student)
            .options(selectinload(Student.school), selectinload(Student.class_group))
            .where(Student.academy_id == ctx.academy_id, Student.active.is_(True))
            .order_by(Student.name)
        )
        if class_id:
            q = q.where(Student.class_id == class_id)
        students = list(db.execute(q).scalars())

        groups: dict[str, list] = {}
        for s in students:
            groups.setdefault(s.class_group.name if s.class_group else "반 미배정", []).append(s)
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] == "반 미배정", kv[0]))

        title = "학생 명단"
        if class_id:
            cg = db.get(ClassGroup, class_id)
            title += f" · {cg.name}" if cg else ""

        ctx.step("PDF 만드는 중", 60)
        pdf = render_pdf(
            "pdf/roster.html",
            dict(
                academy=academy,
                doc_title=title,
                printed_at=datetime.now(KST).strftime("%Y-%m-%d"),
                total=len(students),
                groups=ordered,
            ),
        )
    file_id = ctx.save_file(f"{title}.pdf", "application/pdf", pdf)
    return {"file_id": file_id, "filename": f"{title}.pdf"}
