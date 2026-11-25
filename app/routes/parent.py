"""학부모 화면 (로그인 없음, 링크 /p/{parent_token}).

휴대폰 홈 화면에 추가하면 앱처럼 열리고(PWA), 알림을 켜면 선생님이 기록·공지를 올릴 때 푸시 알림이 옵니다.
"""
import calendar
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .. import care, push
from ..config import APP_NAME, BASE_DIR
from ..db import get_db
from ..models import Academy, CareNotice, CarePhotoStudent, CareReport, ParentPushSub, Student, now
from ..web import render

router = APIRouter(prefix="/p")


def _student(db: Session, token: str) -> Student:
    s = None
    if token and len(token) <= 16:
        s = db.execute(select(Student).options(selectinload(Student.class_group))
                       .where(Student.parent_token == token, Student.active.is_(True))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "링크가 바뀌었거나 없는 주소예요. 학원에 새 링크를 요청해 주세요.")
    return s


@router.get("/sw.js")
def service_worker():
    return FileResponse(BASE_DIR / "static" / "parent-sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/p/"})


@router.get("/{token}/manifest.webmanifest")
def manifest(token: str, db: Session = Depends(get_db)):
    s = _student(db, token)
    academy = db.get(Academy, s.academy_id)
    return JSONResponse({
        "name": f"{APP_NAME} · {s.name} 학습관리",
        "short_name": f"{APP_NAME} {s.name}"[:12],
        "start_url": f"/p/{token}",
        "id": f"/p/{token}",
        "scope": "/p/",
        "display": "standalone",
        "background_color": "#FFF4E6",
        "theme_color": (academy.brand_color if academy and academy.brand_color else "#1B2A5C"),
        "lang": "ko",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


def _calendar(year: int, month: int, attend: dict[date, str]) -> list[list[dict | None]]:
    weeks = []
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(year, month):  # 일요일부터
        weeks.append([{"d": d, "a": attend.get(d, "")} if d.month == month else None for d in week])
    return weeks


@router.get("/{token}")
def home(token: str, request: Request, m: str = "", preview: str = "", db: Session = Depends(get_db)):
    s = _student(db, token)
    academy = db.get(Academy, s.academy_id)
    seen = s.parent_seen_at
    today = care.today()
    try:
        y, mo = (int(x) for x in m.split("-")) if m else (today.year, today.month)
        month0 = date(y, mo, 1)
    except ValueError:
        month0 = today.replace(day=1)

    recs = care.records_for(db, s.id, since=min(month0, today - timedelta(days=62)), limit=120)
    attend = {r["day"]: r["attend"] for r in recs if r["attend"]}
    month_recs = [r for r in recs if r["day"].year == month0.year and r["day"].month == month0.month]
    counts = {k: sum(1 for r in month_recs if r["attend"] == k) for k in care.ATTEND}
    hw_done = [r for r in month_recs if r["hw"]]
    hw_rate = round(100 * sum(1 if r["hw"] == "O" else 0.5 if r["hw"] == "△" else 0 for r in hw_done) / len(hw_done)) if hw_done else None
    scores = [r for r in reversed(recs) if r["pct"] is not None][-12:]

    nq = select(CareNotice).where(CareNotice.academy_id == s.academy_id,
                                  or_(CareNotice.class_id.is_(None), CareNotice.class_id == s.class_id))
    notices = list(db.execute(nq.order_by(CareNotice.important.desc(), CareNotice.id.desc()).limit(10)).scalars())

    reports = list(db.execute(select(CareReport).where(CareReport.student_id == s.id, CareReport.status == "public")
                              .order_by(CareReport.end.desc(), CareReport.id.desc()).limit(12)).scalars())
    photos: dict = {}
    for ph in care.photos_for(db, s.id, start=recs[min(len(recs), 20) - 1]["day"] if recs else today - timedelta(days=30)):
        photos.setdefault(ph.day, []).append(ph)
    shown_days = {r["day"] for r in recs[:20]}
    extra_photo_days = sorted((d for d in photos if d not in shown_days), reverse=True)  # 기록 없이 사진만 있는 날

    is_new = lambda t: bool(seen is None or (t and t > seen))  # noqa: E731
    teacher_view = bool(preview) or bool(request.session.get("teacher_id"))
    if not teacher_view:
        s.parent_seen_at = now()
        db.commit()

    prev_m = (month0 - timedelta(days=1)).replace(day=1)
    next_m = (month0 + timedelta(days=32)).replace(day=1)
    return render(request, "parent/home.html", {
        "student": s, "academy": academy, "token": token, "vapid": push.public_key(),
        "recent": recs[:20], "month0": month0, "weeks": _calendar(month0.year, month0.month, attend), "counts": counts,
        "hw_rate": hw_rate, "scores": scores, "notices": notices, "reports": reports, "photos": photos, "extra_photo_days": extra_photo_days,
        "day_label": care.day_label, "is_new": is_new, "teacher_view": teacher_view,
        "prev_m": prev_m, "next_m": next_m if next_m <= today.replace(day=1) else None, "today": today, "fmt": care.fmt_num,
    })


@router.post("/{token}/subscribe")
async def subscribe(token: str, request: Request, db: Session = Depends(get_db)):
    s = _student(db, token)
    data = await request.json()
    endpoint = str(data.get("endpoint") or "")
    keys = data.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth") or len(endpoint) > 2000:
        raise HTTPException(400, "알림 정보가 올바르지 않아요.")
    ua = request.headers.get("user-agent", "")
    device = "아이폰" if ("iPhone" in ua or "iPad" in ua) else "안드로이드" if "Android" in ua else "PC"
    sub = db.execute(select(ParentPushSub).where(ParentPushSub.student_id == s.id, ParentPushSub.endpoint == endpoint)).scalar_one_or_none()
    if sub is None:
        sub = ParentPushSub(academy_id=s.academy_id, student_id=s.id, endpoint=endpoint)
        db.add(sub)
    sub.p256dh, sub.auth, sub.device, sub.fails = str(keys["p256dh"])[:200], str(keys["auth"])[:100], device, 0
    db.commit()
    return {"ok": True}


@router.get("/{token}/r/{rid}")
def report(token: str, rid: int, request: Request, preview: str = "", db: Session = Depends(get_db)):
    from .care_more import render_report
    s = _student(db, token)
    r = db.get(CareReport, rid)
    if r is None or r.student_id != s.id or r.status != "public":
        raise HTTPException(404, "리포트를 찾을 수 없어요. 선생님이 아직 공개하지 않았을 수 있어요.")
    teacher_view = bool(preview) or bool(request.session.get("teacher_id"))
    if not teacher_view and r.viewed_at is None:
        r.viewed_at = now()
        db.commit()
    return render_report(request, db, r, teacher_view=teacher_view, token=token)


@router.get("/{token}/photo/{pid}.jpg")
def photo(token: str, pid: int, s: str = "", db: Session = Depends(get_db)):
    from .care_more import photo_response
    st = _student(db, token)
    if not db.execute(select(CarePhotoStudent.id).where(CarePhotoStudent.photo_id == pid, CarePhotoStudent.student_id == st.id)).first():
        raise HTTPException(404)
    return photo_response(db, pid, s == "t")
