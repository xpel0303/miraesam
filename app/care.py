"""학습관리 공통: 날짜, 반 기록 + 학생 기록 합치기, 알림 문구."""
import secrets
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CareClassDay, CareRecord, Student

KST = ZoneInfo("Asia/Seoul")
ATTEND = {"출": "출석", "지": "지각", "결": "결석"}
HW = {"O": "완료", "△": "일부", "X": "안 함"}
WEEK = "월화수목금토일"


def today() -> date:
    return datetime.now(KST).date()


def parse_day(text: str | None) -> date:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return today()


def day_label(d: date) -> str:
    return f"{d.month}월 {d.day}일({WEEK[d.weekday()]})"


def num(text) -> float | None:
    try:
        v = float(str(text).strip().replace("점", ""))
    except (TypeError, ValueError):
        return None
    return v


def fmt_num(v: float | None) -> str:
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def ensure_token(db: Session, s: Student) -> str:
    if not s.parent_token:
        tok = secrets.token_urlsafe(9)
        while db.execute(select(Student.id).where(Student.parent_token == tok)).first():
            tok = secrets.token_urlsafe(9)
        s.parent_token = tok
        db.commit()
    return s.parent_token


def merged(rec: CareRecord, cd: CareClassDay | None) -> dict:
    """학생 기록 + 반 기록 → 학부모에게 보일 하루 내용."""
    total = (cd.test_total if cd and cd.test_total else None) or 100
    test_name = (cd.test_name if cd else "") or ""
    score = rec.score
    return {
        "day": rec.day,
        "label": day_label(rec.day),
        "attend": rec.attend,
        "attend_text": ATTEND.get(rec.attend, ""),
        "hw": rec.hw,
        "hw_text": HW.get(rec.hw, ""),
        "hw_name": (cd.hw_name if cd else "") or "",
        "test_name": test_name or ("시험" if score is not None else ""),
        "score": score,
        "total": total,
        "pct": round(100 * score / total) if score is not None and total else None,
        "progress": rec.progress or (cd.progress if cd else "") or "",
        "next_hw": rec.next_hw or (cd.next_hw if cd else "") or "",
        "note": (cd.note if cd else "") or "",
        "comment": rec.comment or "",
        "updated_at": rec.updated_at,
    }


def records_for(db: Session, student_id: int, since: date | None = None, limit: int = 60) -> list[dict]:
    q = select(CareRecord).where(CareRecord.student_id == student_id)
    if since:
        q = q.where(CareRecord.day >= since)
    recs = list(db.execute(q.order_by(CareRecord.day.desc()).limit(limit)).scalars())
    keys = {(r.class_id, r.day) for r in recs if r.class_id}
    cds = {}
    if keys:
        for cd in db.execute(select(CareClassDay).where(CareClassDay.class_id.in_({k[0] for k in keys}),
                                                        CareClassDay.day.in_({k[1] for k in keys}))).scalars():
            cds[(cd.class_id, cd.day)] = cd
    return [merged(r, cds.get((r.class_id, r.day))) for r in recs]


def push_text(name: str, m: dict) -> tuple[str, str]:
    """알림 제목·본문 (휴대폰 알림창에 보이는 글)."""
    parts = []
    if m["attend_text"]:
        parts.append(m["attend_text"])
    if m["hw_text"]:
        parts.append(f"숙제{(' ' + m['hw_name']) if m['hw_name'] else ''} {m['hw_text']}")
    if m["score"] is not None:
        parts.append(f"{m['test_name']} {fmt_num(m['score'])}/{fmt_num(m['total'])}")
    body = " · ".join(parts)
    if m["progress"]:
        body += ("\n" if body else "") + f"진도: {m['progress']}"
    if m["comment"]:
        body += ("\n" if body else "") + m["comment"]
    return f"{name} · {m['label']} 수업 기록", (body or "오늘 수업 기록이 올라왔어요.")[:240]
