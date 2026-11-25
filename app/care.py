"""학습관리 공통: 날짜, 반 기록 + 학생 기록 합치기, 알림 문구."""
import secrets
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CareBook, CareBookUse, CareClassDay, CarePhoto, CarePhotoStudent, CareRecord, Student

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
        "progress": (" / ".join(x for x in ((cd.progress if cd else ""), rec.progress) if x) if rec.prog_items  # 개인 교재 진도는 반 진도에 더해서
                     else rec.progress or (cd.progress if cd else "") or ""),
        "next_hw": rec.next_hw or (cd.next_hw if cd else "") or "",
        "note": (cd.note if cd else "") or "",
        "comment": rec.comment or "",
        "items": _merge_items(rec.prog_items or [], (cd.prog_items or []) if cd else []),
        "class_id": rec.class_id,
        "updated_at": rec.updated_at,
    }


def _merge_items(own: list, cls: list) -> list:
    """학생 개인 진도 + 반 진도 (같은 교재는 학생 것이 우선)."""
    books = {it.get("b") for it in own}
    return list(own) + [it for it in cls if it.get("b") not in books]


def records_for(db: Session, student_id: int, since: date | None = None, limit: int = 60, until: date | None = None) -> list[dict]:
    q = select(CareRecord).where(CareRecord.student_id == student_id)
    if since:
        q = q.where(CareRecord.day >= since)
    if until:
        q = q.where(CareRecord.day <= until)
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


# ── 교재 진도 ────────────────────────────────────────────────────────────────


def clean_items(raw, book_ids: set[int]) -> list[dict]:
    """화면에서 온 진도 목록(JSON)을 검사해 저장할 모양으로."""
    import json
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        try:
            b = int(it.get("b"))
        except (TypeError, ValueError, AttributeError):
            continue
        if b not in book_ids:
            continue
        u = sorted({int(x) for x in (it.get("u") or []) if str(x).lstrip("-").isdigit() and int(x) >= 0})[:200]
        pg = it.get("p") or None
        if pg:
            try:
                a, z = int(pg[0]), int(pg[1] if len(pg) > 1 and str(pg[1]).strip() else pg[0])
                pg = [min(a, z), max(a, z)]
            except (TypeError, ValueError, IndexError):
                pg = None
        if u or pg:
            out.append({"b": b, "u": u, "p": pg})
    return out


def item_label(it: dict, books: dict) -> str:
    b = books.get(it["b"])
    if b is None:
        return ""
    parts = []
    if it.get("u"):
        names = [b.units[i] for i in it["u"] if i < len(b.units or [])]
        parts.append(", ".join(names))
    if it.get("p"):
        a, z = it["p"]
        parts.append(f"p.{a}" + (f"~{z}" if z != a else ""))
    return f"{b.title} {' '.join(parts)}".strip()


def books_for_class(db: Session, academy_id: int, class_id: int, student_ids: list[int]) -> tuple[list[CareBook], dict[int, list[int]]]:
    """반에 연결된 교재 + 학생 개인 교재. (교재 목록, 학생id → 개인 교재 id 목록)"""
    uses = list(db.execute(select(CareBookUse).where(CareBookUse.academy_id == academy_id, CareBookUse.done.is_(False),
                                                     (CareBookUse.class_id == class_id) | (CareBookUse.student_id.in_(student_ids or [0])))).scalars())
    ids = {u.book_id for u in uses}
    books = list(db.execute(select(CareBook).where(CareBook.id.in_(ids or {0}), CareBook.active.is_(True)).order_by(CareBook.title)).scalars())
    personal: dict[int, list[int]] = {}
    for u in uses:
        if u.student_id:
            personal.setdefault(u.student_id, []).append(u.book_id)
    return books, personal


def covered(db: Session, class_id: int, book_ids: list[int]) -> dict[int, dict]:
    """반에서 지금까지 나간 단원·쪽 (교재id → {"u": [단원], "p": 마지막 쪽}). 학생 개인 진도도 포함."""
    out: dict[int, dict] = {b: {"u": set(), "p": 0} for b in book_ids}
    rows = [cd.prog_items for cd in db.execute(select(CareClassDay).where(CareClassDay.class_id == class_id)).scalars()]
    rows += [r.prog_items for r in db.execute(select(CareRecord).where(CareRecord.class_id == class_id)).scalars() if r.prog_items]
    for items in rows:
        for it in items or []:
            if it.get("b") in out:
                out[it["b"]]["u"].update(it.get("u") or [])
                if it.get("p"):
                    out[it["b"]]["p"] = max(out[it["b"]]["p"], it["p"][1])
    return {b: {"u": sorted(v["u"]), "p": v["p"]} for b, v in out.items()}


# ── 사진 ─────────────────────────────────────────────────────────────────────


def process_photo(data: bytes) -> tuple[bytes, bytes, int, int]:
    """휴대폰 사진 → (긴 변 1600 JPEG, 긴 변 480 썸네일, 가로, 세로). 큰 사진도 메모리를 적게 쓰도록 draft 로 먼저 줄여 읽음."""
    import io

    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    if im.format == "JPEG":
        im.draft("RGB", (1600, 1600))
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.convert("RGBA").split()[-1])
        im = bg
    im = im.convert("RGB")
    im.thumbnail((1600, 1600), Image.LANCZOS)
    big = io.BytesIO()
    im.save(big, "JPEG", quality=82, optimize=True, progressive=True)
    w, h = im.size
    im.thumbnail((480, 480), Image.LANCZOS)
    small = io.BytesIO()
    im.save(small, "JPEG", quality=75, optimize=True)
    return big.getvalue(), small.getvalue(), w, h


def photos_for(db: Session, student_id: int, start: date | None = None, end: date | None = None, limit: int = 60) -> list[CarePhoto]:
    q = (select(CarePhoto).join(CarePhotoStudent, CarePhotoStudent.photo_id == CarePhoto.id)
         .where(CarePhotoStudent.student_id == student_id, CarePhoto.day.is_not(None)))
    if start:
        q = q.where(CarePhoto.day >= start)
    if end:
        q = q.where(CarePhoto.day <= end)
    return list(db.execute(q.order_by(CarePhoto.day.desc(), CarePhoto.id.desc()).limit(limit)).scalars())


# ── 기간 리포트 ──────────────────────────────────────────────────────────────


def report_data(db: Session, s: Student, start: date, end: date) -> dict:
    recs = list(reversed(records_for(db, s.id, since=start, until=end, limit=400)))  # 오래된 날부터
    att = {k: sum(1 for r in recs if r["attend"] == k) for k in ATTEND}
    att_n = sum(att.values())
    attend_rate = round(100 * (att["출"] + att["지"]) / att_n) if att_n else None
    hws = [r for r in recs if r["hw"]]
    hw_rate = round(100 * sum(1 if r["hw"] == "O" else 0.5 if r["hw"] == "△" else 0 for r in hws) / len(hws)) if hws else None

    tests = [r for r in recs if r["score"] is not None]
    # 같은 날 같은 반 평균 (100점 환산)
    keys = {(r["class_id"], r["day"]) for r in tests if r["class_id"]}
    class_avg: dict = {}
    if keys:
        rows = db.execute(select(CareRecord.class_id, CareRecord.day, CareRecord.score)
                          .where(CareRecord.class_id.in_({k[0] for k in keys}), CareRecord.day.in_({k[1] for k in keys}),
                                 CareRecord.score.is_not(None))).all()
        totals = {(cd.class_id, cd.day): (cd.test_total or 100) for cd in db.execute(
            select(CareClassDay).where(CareClassDay.class_id.in_({k[0] for k in keys}), CareClassDay.day.in_({k[1] for k in keys}))).scalars()}
        acc: dict = {}
        for cid, d, sc in rows:
            acc.setdefault((cid, d), []).append(100 * sc / (totals.get((cid, d)) or 100))
        class_avg = {k: round(sum(v) / len(v)) for k, v in acc.items() if len(v) >= 2}
    for t in tests:
        t["class_avg"] = class_avg.get((t["class_id"], t["day"]))
    avg = round(sum(t["pct"] for t in tests) / len(tests)) if tests else None

    # 진도: 교재별로 모으기
    by_book: dict[int, dict] = {}
    free: list[dict] = []
    for r in recs:
        if r["items"]:
            for it in r["items"]:
                g = by_book.setdefault(it["b"], {"u": set(), "p": None, "days": set()})
                g["u"].update(it.get("u") or [])
                g["days"].add(r["day"])
                if it.get("p"):
                    a, z = it["p"]
                    g["p"] = [min(a, g["p"][0]), max(z, g["p"][1])] if g["p"] else [a, z]
        elif r["progress"]:
            free.append({"label": r["label"], "text": r["progress"]})
    books = {b.id: b for b in db.execute(select(CareBook).where(CareBook.id.in_(set(by_book) or {0}))).scalars()}
    progress = []
    for bid, g in by_book.items():
        b = books.get(bid)
        if b is None or not b.show:
            continue
        units = [b.units[i] for i in sorted(g["u"]) if i < len(b.units or [])]
        progress.append({"book": b, "units": units, "pages": g["p"], "n_units": len(b.units or []), "done_upto": max(g["u"]) + 1 if g["u"] else 0})
    progress.sort(key=lambda x: x["book"].title)

    photos = photos_for(db, s.id, start, end, limit=24)
    return {"recs": recs, "att": att, "att_n": att_n, "attend_rate": attend_rate, "hws": hws, "hw_rate": hw_rate,
            "tests": tests, "avg": avg, "progress": progress, "free": free, "photos": photos,
            "lessons": len(recs), "next_hw": next((r["next_hw"] for r in reversed(recs) if r["next_hw"]), "")}


def month_range(ym: str | None = None) -> tuple[date, date]:
    from calendar import monthrange
    t = today()
    try:
        y, m = (int(x) for x in (ym or "").split("-"))
        date(y, m, 1)
    except ValueError:
        y, m = t.year, t.month
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])
