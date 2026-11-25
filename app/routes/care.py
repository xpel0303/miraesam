"""학습관리 (선생님): 매일 기록 표 · 학부모 앱 연결 · 공지 · 알림 기록.

학부모 화면은 routes/parent.py (/p/{parent_token}), 알림 보내기는 push.py.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .. import care, push
from ..db import get_db
from ..models import CareBook, CareBookUse, CareClassDay, CareNotice, CareRecord, ClassGroup, PushLog, ParentPushSub, Student, Teacher, now
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/care")


def _classes(db: Session, aid: int) -> list[ClassGroup]:
    return list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == aid, ClassGroup.active.is_(True))
                           .order_by(ClassGroup.name)).scalars())


def _class(db: Session, cid, aid: int) -> ClassGroup:
    c = db.get(ClassGroup, int(cid)) if str(cid).isdigit() else None
    if c is None or c.academy_id != aid:
        raise HTTPException(404, "반을 찾을 수 없습니다.")
    return c


def _students(db: Session, aid: int, class_id: int | None = None) -> list[Student]:
    q = select(Student).options(selectinload(Student.class_group)).where(Student.academy_id == aid, Student.active.is_(True))
    if class_id:
        q = q.where(Student.class_id == class_id)
    return list(db.execute(q.order_by(Student.name)).scalars())


def _student(db: Session, sid: int, aid: int) -> Student:
    s = db.get(Student, sid)
    if s is None or s.academy_id != aid:
        raise HTTPException(404, "학생을 찾을 수 없습니다.")
    return s


def _sub_counts(db: Session, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict(db.execute(select(ParentPushSub.student_id, func.count()).where(ParentPushSub.student_id.in_(ids)).group_by(ParentPushSub.student_id)).all())


def _parent_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]  # Render 는 프록시 뒤라 http 로 보임
    return f"{base}/p/{token}"


# ── 매일 기록 ────────────────────────────────────────────────────────────────


@router.get("")
def daily(request: Request, class_id: str = "", day: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    classes = _classes(db, aid)
    if not classes:
        return render(request, "care/daily.html", {"teacher": teacher, "active": "care", "classes": [], "tab": "daily"})
    cls = _class(db, class_id, aid) if class_id else classes[0]
    d = care.parse_day(day)
    students = _students(db, aid, cls.id)
    cd = db.execute(select(CareClassDay).where(CareClassDay.class_id == cls.id, CareClassDay.day == d)).scalar_one_or_none()
    recs = {r.student_id: r for r in db.execute(select(CareRecord).where(CareRecord.day == d, CareRecord.student_id.in_([s.id for s in students] or [0]))).scalars()}
    prev = db.execute(select(CareClassDay).where(CareClassDay.class_id == cls.id, CareClassDay.day < d).order_by(CareClassDay.day.desc()).limit(1)).scalar_one_or_none()
    # 이번 달 이 반에 기록이 있는 날 (날짜 옆 점 표시)
    first = d.replace(day=1)
    days_with = {x for (x,) in db.execute(select(CareClassDay.day).where(CareClassDay.class_id == cls.id, CareClassDay.day >= first,
                                                                          CareClassDay.day < (first + timedelta(days=32)).replace(day=1)))}
    books, personal = care.books_for_class(db, aid, cls.id, [s.id for s in students])
    cov = care.covered(db, cls.id, [b.id for b in books])
    book_js = [{"id": b.id, "title": b.title, "mode": b.mode, "units": b.units or [], "color": b.color, "pages": b.total_pages,
                "done": cov[b.id]["u"], "last_page": cov[b.id]["p"],
                "personal": [sid for sid, bids in personal.items() if b.id in bids]} for b in books]
    class_book_ids = {u for (u,) in db.execute(select(CareBookUse.book_id).where(CareBookUse.class_id == cls.id, CareBookUse.done.is_(False)))}
    for bj in book_js:
        bj["class"] = bj["id"] in class_book_ids
    return render(request, "care/daily.html", {
        "books": book_js, "teacher": teacher, "active": "care", "tab": "daily", "classes": classes, "cls": cls, "day": d, "day_label": care.day_label(d),
        "prev_day": d - timedelta(days=1), "next_day": d + timedelta(days=1), "today": care.today(),
        "students": students, "cd": cd, "recs": recs, "prev": prev, "subs": _sub_counts(db, [s.id for s in students]),
        "days_with": sorted(days_with), "fmt": care.fmt_num, "ATTEND": care.ATTEND, "HW": care.HW,
    })


@router.post("")
async def daily_save(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    form = await request.form()
    aid = teacher.academy_id
    cls = _class(db, form.get("class_id", ""), aid)
    d = care.parse_day(form.get("day"))
    g = lambda k: str(form.get(k, "") or "").strip()  # noqa: E731

    cd = db.execute(select(CareClassDay).where(CareClassDay.class_id == cls.id, CareClassDay.day == d)).scalar_one_or_none()
    if cd is None:
        cd = CareClassDay(academy_id=aid, class_id=cls.id, day=d)
        db.add(cd)
    cd.hw_name, cd.test_name, cd.test_total = g("hw_name")[:120], g("test_name")[:80], care.num(g("test_total"))
    cd.progress, cd.next_hw, cd.note = g("progress"), g("next_hw"), g("note")
    book_ids = {b for (b,) in db.execute(select(CareBook.id).where(CareBook.academy_id == aid))}
    cd.prog_items = care.clean_items(form.get("prog_items", ""), book_ids)

    students = _students(db, aid, cls.id)
    recs = {r.student_id: r for r in db.execute(select(CareRecord).where(CareRecord.day == d, CareRecord.student_id.in_([s.id for s in students] or [0]))).scalars()}
    saved: list[CareRecord] = []
    for s in students:
        vals = {"attend": g(f"a_{s.id}"), "hw": g(f"h_{s.id}"), "score": care.num(g(f"s_{s.id}")),
                "progress": g(f"p_{s.id}"), "next_hw": g(f"n_{s.id}"), "comment": g(f"c_{s.id}"),
                "prog_items": care.clean_items(form.get(f"pi_{s.id}", ""), book_ids)}
        vals["attend"] = vals["attend"] if vals["attend"] in care.ATTEND else ""
        vals["hw"] = vals["hw"] if vals["hw"] in care.HW else ""
        r = recs.get(s.id)
        if not any(v not in ("", None, []) for v in vals.values()):
            if r is not None:
                db.delete(r)
            continue
        if r is None:
            r = CareRecord(academy_id=aid, student_id=s.id, day=d)
            db.add(r)
        r.class_id = cls.id
        changed = any(getattr(r, k) != v for k, v in vals.items())
        for k, v in vals.items():
            setattr(r, k, v)
        if changed:
            r.updated_at = now()
        saved.append(r)
    db.commit()

    msg = f"{cls.name} {care.day_label(d)} 기록을 저장했어요."
    if form.get("notify") == "1" and saved:
        by_id = {s.id: s for s in students}
        messages = []
        for r in saved:
            s = by_id[r.student_id]
            title, body = care.push_text(s.name, care.merged(r, cd))
            messages.append({"student_id": s.id, "title": title, "body": body, "url": f"/p/{care.ensure_token(db, s)}"})
        cd.notified_at = now()
        db.commit()
        on = sum(1 for sid, n in _sub_counts(db, [r.student_id for r in saved]).items() if n)
        push.deliver_later(aid, messages, "record")
        if on:
            msg += f" 학부모 앱 알림을 {on}명에게 보내는 중이에요."
        if len(saved) > on:
            msg += f" 앱 알림을 아직 안 켠 {len(saved) - on}명은 '학부모 앱 연결' 탭에서 안내 문구를 보내 주세요."
    flash(request, msg)
    return RedirectResponse(f"/care?class_id={cls.id}&day={d.isoformat()}", status_code=303)


# ── 학부모 앱 연결 ────────────────────────────────────────────────────────────


@router.get("/parents")
def parents(request: Request, class_id: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    classes = _classes(db, aid)
    cls = _class(db, class_id, aid) if class_id else None
    students = _students(db, aid, cls.id if cls else None)
    for s in students:
        care.ensure_token(db, s)
    subs = _sub_counts(db, [s.id for s in students])
    links = {s.id: _parent_url(request, s.parent_token) for s in students}
    return render(request, "care/parents.html", {"teacher": teacher, "active": "care", "tab": "parents", "classes": classes, "cls": cls,
                                                 "students": students, "subs": subs, "links": links,
                                                 "on": sum(1 for s in students if subs.get(s.id)), "academy": teacher.academy})


@router.post("/parents/{sid}/test")
def parent_test(sid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    s = _student(db, sid, teacher.academy_id)
    if not _sub_counts(db, [s.id]).get(s.id):
        flash(request, f"{s.name} 학부모님은 아직 앱 알림을 켜지 않으셨어요.", "error")
    else:
        push.deliver_later(s.academy_id, [{"student_id": s.id, "title": f"{teacher.academy.name or '미래샘'} 알림 확인",
                                           "body": f"{s.name} 학습관리 알림이 잘 켜져 있어요.", "url": f"/p/{care.ensure_token(db, s)}"}], "test")
        flash(request, f"{s.name} 학부모님 휴대폰으로 시험 알림을 보냈어요. '알림 기록' 탭에서 결과를 볼 수 있어요.")
    return RedirectResponse(request.headers.get("referer") or "/care/parents", status_code=303)


@router.post("/parents/{sid}/reset")
def parent_reset(sid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    """링크가 엉뚱한 사람에게 갔을 때: 새 링크로 바꾸고 기존 알림 연결을 끊음."""
    s = _student(db, sid, teacher.academy_id)
    s.parent_token = None
    db.execute(delete(ParentPushSub).where(ParentPushSub.student_id == s.id))
    db.commit()
    care.ensure_token(db, s)
    flash(request, f"{s.name} 학부모 링크를 새로 만들었어요. 예전 링크는 이제 열리지 않아요. 새 링크를 다시 보내 주세요.")
    return RedirectResponse(request.headers.get("referer") or "/care/parents", status_code=303)


# ── 공지 ─────────────────────────────────────────────────────────────────────


@router.get("/notices")
def notices(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    items = list(db.execute(select(CareNotice).options(selectinload(CareNotice.class_group)).where(CareNotice.academy_id == teacher.academy_id)
                            .order_by(CareNotice.id.desc()).limit(100)).scalars())
    return render(request, "care/notices.html", {"teacher": teacher, "active": "care", "tab": "notices", "items": items,
                                                 "classes": _classes(db, teacher.academy_id)})


@router.post("/notices")
def notice_add(request: Request, title: str = Form(...), body: str = Form(""), class_id: str = Form(""), important: str = Form(""),
               notify: str = Form(""), db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    cls = _class(db, class_id, aid) if class_id else None
    n = CareNotice(academy_id=aid, class_id=cls.id if cls else None, title=title.strip()[:120] or "공지", body=body.strip(),
                   important=bool(important), teacher_id=teacher.id)
    db.add(n)
    db.commit()
    msg = f"공지 '{n.title}'를 {'[' + cls.name + '] 학부모 화면' if cls else '모든 학부모 화면'}에 올렸어요."
    if notify:
        targets = _students(db, aid, cls.id if cls else None)
        preview = " ".join(n.body.split())[:100]
        messages = [{"student_id": s.id, "title": f"[공지] {n.title}", "body": preview or "새 공지가 올라왔어요.",
                     "url": f"/p/{care.ensure_token(db, s)}#notices"} for s in targets]
        push.deliver_later(aid, messages, "notice")
        on = sum(1 for v in _sub_counts(db, [s.id for s in targets]).values() if v)
        msg += f" 앱 알림을 켠 학부모 {on}명에게 알림을 보내는 중이에요." if on else " (아직 앱 알림을 켠 학부모님이 없어요)"
    flash(request, msg)
    return RedirectResponse("/care/notices", status_code=303)


@router.post("/notices/{nid}/delete")
def notice_delete(nid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    n = db.get(CareNotice, nid)
    if n is None or n.academy_id != teacher.academy_id:
        raise HTTPException(404, "공지를 찾을 수 없습니다.")
    db.delete(n)
    db.commit()
    flash(request, "공지를 지웠어요.")
    return RedirectResponse("/care/notices", status_code=303)


# ── 알림 기록 ────────────────────────────────────────────────────────────────


@router.get("/log")
def log(request: Request, status: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    q = select(PushLog).options(selectinload(PushLog.student)).where(PushLog.academy_id == teacher.academy_id)
    if status in ("sent", "fail", "none"):
        q = q.where(PushLog.status == status)
    items = list(db.execute(q.order_by(PushLog.id.desc()).limit(300)).scalars())
    return render(request, "care/log.html", {"teacher": teacher, "active": "care", "tab": "log", "items": items, "status": status})
