"""학생·반 리포트 작업 (AI 없음).

report_pdf kind:
  class    반 리포트 PDF (분포·성적표·문항 분석·유형별)
  students 학생별 리포트 PDF (한 명당 한 쪽, 반 전체)
  one      학생 한 명 리포트 PDF
  image    결과 요약 한 장 → PNG (단톡방·블로그 게시용)
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from ..db import session_scope
from ..jobs import JobContext, job_handler
from ..models import Academy, ClassGroup, Exam
from ..pdf import render_pdf
from ..reports import data, spec


def _png(pdf: bytes, width_px: int = 1080) -> bytes:
    """PDF 첫 쪽 → 가로 1080px PNG (카톡·인스타에 맞는 크기)."""
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    page = doc[0]
    img = page.render(scale=width_px / page.get_size()[0]).to_pil()
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@job_handler("report_pdf")
def make(ctx: JobContext) -> dict:
    p = ctx.payload
    ctx.step("채점하는 중", 15)
    with session_scope() as db:
        e = db.get(Exam, p["exam_id"])
        academy = db.get(Academy, e.academy_id)
        db.expunge(academy)
        cid = p.get("class_id")
        cls = db.get(ClassGroup, cid) if cid else None
        S, students, responses = data.summary(db, e, cid)
        items = spec.clean_items(e.items or [])
        exam = {"title": e.title, "date": e.exam_date, "school": e.school.name if e.school else "",
                "classes": cls.name if cls else ", ".join(c.name for c in e.classes)}
        grades = {s.id: s.grade or "" for s in students}
    if not S["n"]:
        raise RuntimeError("답을 입력한 학생이 없습니다.")
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    kind = p["kind"]
    scope = f" · {exam['classes']}" if exam["classes"] else ""
    base = {"academy": academy, "exam": exam, "S": S, "items": items, "fmt": spec.fmt, "circ": spec.CIRCLED, "today": today,
            "mask": spec.mask_name if p.get("mask") else (lambda n: n), "easy": spec.EASY_MISS}

    if kind in ("students", "one"):
        ids = [p["student_id"]] if kind == "one" else [x["id"] for x in sorted(S["people"], key=lambda x: (x["class_name"], x["name"]))]
        views = []
        for sid in ids:
            v = spec.student_view(S, sid)
            if v:
                views.append({**v, "grade": grades.get(sid, ""), "school": v["me"]["school"]})
        if not views:
            raise RuntimeError("이 학생은 아직 답이 없습니다.")
        ctx.step(f"학생 {len(views)}명 리포트 만드는 중", 50)
        pdf = render_pdf("pdf/report_student.html", {**base, "views": views, "show_rank": p.get("show_rank"), "doc_title": f"{exam['title']} · 학생 리포트"})
        name = f"{exam['title']}_{views[0]['me']['name']}_리포트.pdf" if kind == "one" else f"{exam['title']}{scope.replace(' · ', '_')}_학생별리포트.pdf"
        return {"file_id": ctx.save_file(name, "application/pdf", pdf), "filename": name}

    worst = sorted([it for it in S["items"] if it["rate"] is not None], key=lambda it: it["rate"])[:8]
    if kind == "class":
        ctx.step("반 리포트 만드는 중", 50)
        pdf = render_pdf("pdf/report_class.html", {**base, "show_names": p.get("show_names", True), "worst": worst, "doc_title": f"{exam['title']} · 반 리포트"})
        name = f"{exam['title']}{scope.replace(' · ', '_')}_반리포트.pdf"
        return {"file_id": ctx.save_file(name, "application/pdf", pdf), "filename": name}

    ctx.step("결과 이미지 만드는 중", 50)
    top = [x for x in S["people"] if x["rank"] <= 3][:5] if p.get("show_names") else []
    pdf = render_pdf("pdf/report_image.html", {**base, "worst": worst, "top": top, "doc_title": ""})
    ctx.step("이미지로 바꾸는 중", 80)
    png = _png(pdf)
    name = f"{exam['title']}{scope.replace(' · ', '_')}_결과.png"
    return {"file_id": ctx.save_file(name, "image/png", png), "filename": name}
