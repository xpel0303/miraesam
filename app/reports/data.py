"""시험에 응시한(또는 응시해야 할) 학생 목록을 DB에서 모읍니다. 화면과 PDF 작업이 같이 씁니다."""
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Exam, ExamResponse, Student
from . import spec


def roster(db: Session, exam: Exam, class_id: int | None = None) -> tuple[list[Student], dict[int, ExamResponse]]:
    """(학생 목록, {student_id: 답안}). 내준 반의 재원생 + 답안이 이미 있는 학생(반을 옮겼어도)."""
    responses = {r.student_id: r for r in db.execute(select(ExamResponse).where(ExamResponse.exam_id == exam.id)).scalars()}
    class_ids = [c.id for c in exam.classes]
    q = select(Student).options(selectinload(Student.class_group), selectinload(Student.school)).where(Student.academy_id == exam.academy_id)
    students = []
    if class_ids or responses:
        conds = []
        if class_ids:
            conds.append((Student.class_id.in_(class_ids)) & Student.active.is_(True))
        if responses:
            conds.append(Student.id.in_(list(responses)))
        cond = conds[0] if len(conds) == 1 else (conds[0] | conds[1])
        students = list(db.execute(q.where(cond)).scalars())
    if class_id:
        students = [s for s in students if s.class_id == class_id]
    students.sort(key=lambda s: ((s.class_group.name if s.class_group else "~"), s.name))
    return students, responses


def people(students: list[Student], responses: dict[int, ExamResponse]) -> list[dict]:
    """답안이 있는 학생만 → spec.summarize 입력."""
    out = []
    for s in students:
        r = responses.get(s.id)
        if r is None or not (r.answers or r.sa_scores):
            continue
        out.append({"id": s.id, "name": s.name, "class_name": s.class_group.name if s.class_group else "",
                    "school": s.school.name if s.school else "", "answers": r.answers or {}, "sa_scores": r.sa_scores or {},
                    "sa_text": r.sa_text or {}, "comment": r.comment or "", "by": r.by})
    return out


def summary(db: Session, exam: Exam, class_id: int | None = None) -> tuple[dict, list[Student], dict[int, ExamResponse]]:
    students, responses = roster(db, exam, class_id)
    items = spec.clean_items(exam.items or [])
    return spec.summarize(items, people(students, responses)), students, responses
