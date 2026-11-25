"""학습관리 2: 교재(목차) · 사진 · 월간 리포트. 매일 기록·학부모 앱은 routes/care.py, 학부모 화면은 routes/parent.py."""
import asyncio
import io
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload, undefer

from .. import care, llm, push
from ..config import DEFAULT_GEMINI_MODEL
from ..db import get_db
from ..models import (Academy, CareBook, CareBookUse, CarePhoto, CarePhotoStudent, CareReport, CareUploadToken, ClassGroup, Student, Teacher,
                      now)
from ..security import decrypt
from ..web import current_teacher, flash, render

router = APIRouter()

COLORS = ["#7FB3E6", "#F2A7A0", "#9ED8B4", "#F6D57A", "#C3A8E8", "#F5B97F", "#8FD3D8", "#D9D9D9"]
MAX_PHOTO = 20 * 1024 * 1024


def _classes(db: Session, aid: int) -> list[ClassGroup]:
    return list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == aid, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())


def _students(db: Session, aid: int, class_id: int | None = None) -> list[Student]:
    q = select(Student).options(selectinload(Student.class_group)).where(Student.academy_id == aid, Student.active.is_(True))
    if class_id:
        q = q.where(Student.class_id == class_id)
    return list(db.execute(q.order_by(Student.name)).scalars())


def _own(db: Session, model, oid: int, aid: int):
    obj = db.get(model, oid)
    if obj is None or obj.academy_id != aid:
        raise HTTPException(404, "찾을 수 없습니다.")
    return obj


def _back(request: Request, default: str) -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer") or default, status_code=303)


# ── 교재 ─────────────────────────────────────────────────────────────────────


def _units(text: str) -> list[str]:
    return [ln.strip()[:120] for ln in (text or "").splitlines() if ln.strip()][:300]


def _cover(data: bytes) -> bytes | None:
    if not data:
        return None
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        if im.format == "JPEG":
            im.draft("RGB", (600, 600))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((360, 480), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


@router.get("/care/books")
def books(request: Request, done: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    items = list(db.execute(select(CareBook).where(CareBook.academy_id == aid, CareBook.active.is_(not done)).order_by(CareBook.title)).scalars())
    uses: dict[int, list[CareBookUse]] = {}
    for u in db.execute(select(CareBookUse).options(selectinload(CareBookUse.class_group), selectinload(CareBookUse.student))
                        .where(CareBookUse.academy_id == aid)).scalars():
        uses.setdefault(u.book_id, []).append(u)
    return render(request, "care/books.html", {"teacher": teacher, "active": "care", "tab": "books", "items": items, "uses": uses,
                                               "done": bool(done), "colors": COLORS, "classes": _classes(db, aid)})


async def _book_form(request: Request, b: CareBook):
    form = await request.form()
    g = lambda k: str(form.get(k, "") or "").strip()  # noqa: E731
    b.title = g("title")[:120] or b.title or "교재"
    b.publisher = g("publisher")[:60]
    b.mode = "page" if g("mode") == "page" else "unit"
    b.units = _units(g("units"))
    b.total_pages = int(g("total_pages")) if g("total_pages").isdigit() else None
    b.color = g("color") if g("color").startswith("#") and len(g("color")) <= 9 else b.color or COLORS[0]
    b.note = g("note")
    b.show = bool(form.get("show"))
    f = form.get("cover")
    if f is not None and hasattr(f, "read"):
        data = await f.read()
        cov = _cover(data) if data else None
        if cov:
            b.cover, b.has_cover = cov, True
    if form.get("cover_remove"):
        b.cover, b.has_cover = None, False
    return form


@router.post("/care/books")
async def book_add(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = CareBook(academy_id=teacher.academy_id, title="교재")
    form = await _book_form(request, b)
    db.add(b)
    db.flush()
    cid = str(form.get("class_id") or "")
    if cid.isdigit():
        c = _own(db, ClassGroup, int(cid), teacher.academy_id)
        db.add(CareBookUse(academy_id=teacher.academy_id, book_id=b.id, class_id=c.id))
    db.commit()
    flash(request, f"'{b.title}'를 등록했어요." + ("" if b.units or b.mode == "page" else " 목차를 넣으면 매일 기록에서 단원을 눌러 진도를 넣을 수 있어요."))
    return RedirectResponse(f"/care/books/{b.id}", status_code=303)


@router.get("/care/books/{bid}")
def book_page(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    b = _own(db, CareBook, bid, aid)
    uses = list(db.execute(select(CareBookUse).options(selectinload(CareBookUse.class_group), selectinload(CareBookUse.student))
                           .where(CareBookUse.book_id == b.id).order_by(CareBookUse.done, CareBookUse.id)).scalars())
    cov = {}
    for u in uses:
        if u.class_id:
            cov[u.id] = care.covered(db, u.class_id, [b.id])[b.id]
    return render(request, "care/book.html", {"teacher": teacher, "active": "care", "tab": "books", "b": b, "uses": uses, "cov": cov,
                                              "colors": COLORS, "classes": _classes(db, aid), "students": _students(db, aid)})


@router.post("/care/books/{bid}")
async def book_save(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, CareBook, bid, teacher.academy_id)
    await _book_form(request, b)
    db.commit()
    flash(request, "교재를 저장했어요.")
    return RedirectResponse(f"/care/books/{b.id}", status_code=303)


@router.post("/care/books/{bid}/active")
def book_active(bid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    b = _own(db, CareBook, bid, teacher.academy_id)
    b.active = not b.active
    db.commit()
    flash(request, f"'{b.title}'를 {'다시 쓰는 교재로 옮겼어요' if b.active else '보관함으로 옮겼어요 (지난 진도·리포트는 그대로)'}.")
    return RedirectResponse("/care/books", status_code=303)


@router.post("/care/books/{bid}/use")
def book_use(bid: int, request: Request, target: str = Form(""), db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    b = _own(db, CareBook, bid, aid)
    kind, _, oid = target.partition(":")
    if not oid.isdigit():
        flash(request, "반이나 학생을 골라 주세요.", "error")
        return _back(request, f"/care/books/{b.id}")
    if kind == "c":
        c = _own(db, ClassGroup, int(oid), aid)
        exists = db.execute(select(CareBookUse).where(CareBookUse.book_id == b.id, CareBookUse.class_id == c.id)).scalar_one_or_none()
        if exists is None:
            db.add(CareBookUse(academy_id=aid, book_id=b.id, class_id=c.id))
        else:
            exists.done = False
        name = c.name
    else:
        s = _own(db, Student, int(oid), aid)
        exists = db.execute(select(CareBookUse).where(CareBookUse.book_id == b.id, CareBookUse.student_id == s.id)).scalar_one_or_none()
        if exists is None:
            db.add(CareBookUse(academy_id=aid, book_id=b.id, student_id=s.id))
        else:
            exists.done = False
        name = f"{s.name} (개인)"
    db.commit()
    flash(request, f"'{b.title}'를 {name}에 연결했어요.")
    return _back(request, f"/care/books/{b.id}")


@router.post("/care/books/use/{uid}/{action}")
def book_use_action(uid: int, action: str, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    u = _own(db, CareBookUse, uid, teacher.academy_id)
    if action == "done":
        u.done = not u.done
    elif action == "delete":
        db.delete(u)
    db.commit()
    return _back(request, "/care/books")


@router.get("/care/cover/{bid}.jpg")
def book_cover(bid: int, db: Session = Depends(get_db)):
    """교재 표지 (학부모 화면에서도 보이므로 로그인 없이). 표지는 개인정보가 아님."""
    b = db.execute(select(CareBook).options(undefer(CareBook.cover)).where(CareBook.id == bid)).scalar_one_or_none()
    if b is None or not b.cover:
        raise HTTPException(404)
    return Response(b.cover, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


TOC_SYSTEM = """당신은 교재 목차 사진을 읽어 '진도 단원 목록'을 만드는 사람입니다. JSON 으로만 답합니다.
형식: {"title": "교재 이름(사진에 보이면, 없으면 빈 글자)", "units": ["1강 글의 목적", "2강 심경·분위기", ...]}
- 수업 진도를 나가는 가장 작은 단위(강·Unit·Lesson·Chapter·Day·회 등) 하나를 한 줄로, 책에 나온 순서대로.
- 번호와 단원 이름을 함께 적고(예: "Unit 3 Food Culture"), 쪽 번호·점선·장식 글자는 뺍니다.
- 큰 단원(Part, 대단원) 아래에 작은 단원이 있으면 작은 단원만 적되, 이름 앞에 큰 단원을 짧게 붙여도 됩니다(예: "Part 2 · 5강 함축 의미").
- 정답과 해설, 부록, 머리말, 차례 같은 것은 넣지 않습니다. Mini Test·실전 모의고사처럼 수업에서 푸는 것은 넣습니다.
- 사진이 여러 장이면 이어지는 목차로 보고 하나의 목록으로 합칩니다. 300개 넘게 적지 않습니다."""


@router.post("/care/book-toc")
async def book_toc(files: list[UploadFile], teacher: Teacher = Depends(current_teacher)):
    """목차 사진 → 단원 목록 (Gemini)."""
    key = decrypt(teacher.gemini_key_enc)
    if not key:
        return JSONResponse({"error": "Gemini API 키가 없어요. 설정 화면에서 키를 먼저 넣어 주세요. (목차는 글로 붙여 넣어도 돼요)"}, status_code=400)
    got, total = [], 0
    for f in files or []:
        data = await f.read()
        total += len(data)
        if total > 30 * 1024 * 1024:
            return JSONResponse({"error": "사진이 너무 커요 (모두 합쳐 30MB까지)."}, status_code=400)
        if data:
            got.append((f.filename or "", data))
    if not got:
        return JSONResponse({"error": "목차 사진을 골라 주세요."}, status_code=400)
    if len(got) > 6:
        return JSONResponse({"error": "목차 사진은 한 번에 6장까지예요."}, status_code=400)

    def prep():
        from ..exam import pages
        out = []
        for name, data in got:
            ims = pages.render_pdf(data) if (name.lower().endswith(".pdf") or data[:4] == b"%PDF") else [pages.open_image(data)]
            out += [llm.Image(pages.to_jpeg(pages.normalize(im)), "image/jpeg") for im in ims[:6]]
        return out[:6]

    try:
        imgs = await asyncio.to_thread(prep)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"사진을 읽지 못했어요: {e}"}, status_code=400)
    try:
        res = await asyncio.to_thread(llm.generate, key, f"목차 사진 {len(imgs)}장입니다. 단원 목록을 만들어 주세요.", system=TOC_SYSTEM,
                                      images=imgs, model=teacher.gemini_model or DEFAULT_GEMINI_MODEL, json_mode=True, temperature=0.1, max_tokens=8000)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    units = [str(u).strip()[:120] for u in (res.get("units") or []) if str(u).strip()] if isinstance(res, dict) else []
    if not units:
        return JSONResponse({"error": "사진에서 목차를 찾지 못했어요. 목차 쪽이 잘 보이게 다시 찍어 주세요."}, status_code=422)
    return {"title": str(res.get("title") or "").strip()[:120], "units": units[:300]}


# ── 사진 ─────────────────────────────────────────────────────────────────────


def _save_photos(db: Session, aid: int, tid: int | None, files: list[tuple[str, bytes]]) -> tuple[int, list[str]]:
    n, bad = 0, []
    for name, data in files:
        try:
            big, small, w, h = care.process_photo(data)
        except Exception:  # noqa: BLE001
            bad.append(name or "사진")
            continue
        db.add(CarePhoto(academy_id=aid, teacher_id=tid, data=big, thumb=small, width=w, height=h))
        db.commit()  # 한 장씩 저장 (메모리·중간 실패 대비)
        n += 1
    return n, bad


async def _read_files(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out = []
    for f in (files or [])[:40]:
        data = await f.read(MAX_PHOTO + 1)
        if data and len(data) <= MAX_PHOTO:
            out.append((f.filename or "", data))
    return out


@router.get("/care/photos")
def photos(request: Request, student_id: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    linked = select(CarePhotoStudent.photo_id)
    pending = list(db.execute(select(CarePhoto).where(CarePhoto.academy_id == aid, CarePhoto.id.not_in(linked)).order_by(CarePhoto.id.desc()).limit(200)).scalars())
    q = select(CarePhoto).where(CarePhoto.academy_id == aid, CarePhoto.id.in_(linked))
    if student_id.isdigit():
        q = q.where(CarePhoto.id.in_(select(CarePhotoStudent.photo_id).where(CarePhotoStudent.student_id == int(student_id))))
    done = list(db.execute(q.order_by(CarePhoto.day.desc(), CarePhoto.id.desc()).limit(120)).scalars())
    names: dict[int, list[str]] = {}
    ids = [p.id for p in done]
    if ids:
        for pid, nm in db.execute(select(CarePhotoStudent.photo_id, Student.name).join(Student, Student.id == CarePhotoStudent.student_id)
                                  .where(CarePhotoStudent.photo_id.in_(ids))):
            names.setdefault(pid, []).append(nm)
    return render(request, "care/photos.html", {"teacher": teacher, "active": "care", "tab": "photos", "pending": pending, "done": done,
                                                "names": names, "students": _students(db, aid), "classes": _classes(db, aid),
                                                "student_id": int(student_id) if student_id.isdigit() else None, "today": care.today()})


@router.post("/care/photos/upload")
async def photos_upload(files: list[UploadFile], db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    got = await _read_files(files)
    n, bad = await asyncio.to_thread(_save_photos, db, teacher.academy_id, teacher.id, got)
    return {"ok": n, "bad": bad}


@router.post("/care/photos/qr")
def photos_qr(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    tok = secrets.token_urlsafe(12)
    db.add(CareUploadToken(token=tok, academy_id=teacher.academy_id, teacher_id=teacher.id, expires_at=now() + timedelta(hours=2)))
    db.execute(delete(CareUploadToken).where(CareUploadToken.expires_at < now()))
    db.commit()
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    return {"url": f"{base}/up/{tok}"}


def _upload_token(db: Session, token: str) -> CareUploadToken:
    t = db.get(CareUploadToken, token) if token and len(token) <= 24 else None
    if t is None or t.expires_at < now():
        raise HTTPException(404, "QR 주소가 끝났어요 (2시간). 컴퓨터 화면에서 QR을 다시 띄워 주세요.")
    return t


@router.get("/up/{token}")
def up_page(token: str, request: Request, db: Session = Depends(get_db)):
    t = _upload_token(db, token)
    return render(request, "care/upload.html", {"token": token, "academy": db.get(Academy, t.academy_id)})


@router.post("/up/{token}")
async def up_post(token: str, files: list[UploadFile], db: Session = Depends(get_db)):
    t = _upload_token(db, token)
    got = await _read_files(files)
    n, bad = await asyncio.to_thread(_save_photos, db, t.academy_id, t.teacher_id, got)
    return {"ok": n, "bad": bad}


@router.post("/care/photos/assign")
async def photos_assign(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    form = await request.form()
    aid = teacher.academy_id
    pids = [int(x) for x in str(form.get("photo_ids", "")).split(",") if x.strip().isdigit()]
    sids = {int(x) for x in form.getlist("student_ids") if str(x).isdigit()}
    ok_sids = {s.id for s in db.execute(select(Student).where(Student.academy_id == aid, Student.id.in_(sids or {0}))).scalars()}
    photos_ = list(db.execute(select(CarePhoto).where(CarePhoto.academy_id == aid, CarePhoto.id.in_(pids or [0]))).scalars())
    if not photos_ or not ok_sids:
        flash(request, "사진과 학생을 골라 주세요.", "error")
        return RedirectResponse("/care/photos", status_code=303)
    d = care.parse_day(str(form.get("day", "")))
    memo = str(form.get("memo", "") or "").strip()[:300]
    replace = bool(form.get("replace"))
    for p in photos_:
        p.day = d
        if memo or replace:
            p.memo = memo
        if replace:
            db.execute(delete(CarePhotoStudent).where(CarePhotoStudent.photo_id == p.id))
        have = {sid for (sid,) in db.execute(select(CarePhotoStudent.student_id).where(CarePhotoStudent.photo_id == p.id))}
        for sid in ok_sids - have:
            db.add(CarePhotoStudent(photo_id=p.id, student_id=sid))
    db.commit()
    flash(request, f"사진 {len(photos_)}장을 학생 {len(ok_sids)}명의 {care.day_label(d)} 기록에 올렸어요. 학부모 화면에 바로 보여요.")
    return RedirectResponse("/care/photos", status_code=303)


@router.post("/care/photos/delete")
async def photos_delete(request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    form = await request.form()
    pids = [int(x) for x in str(form.get("photo_ids", "")).split(",") if x.strip().isdigit()]
    ids = [p for (p,) in db.execute(select(CarePhoto.id).where(CarePhoto.academy_id == teacher.academy_id, CarePhoto.id.in_(pids or [0])))]
    if ids:
        db.execute(delete(CarePhotoStudent).where(CarePhotoStudent.photo_id.in_(ids)))
        db.execute(delete(CarePhoto).where(CarePhoto.id.in_(ids)))
        db.commit()
    flash(request, f"사진 {len(ids)}장을 지웠어요.")
    return RedirectResponse("/care/photos", status_code=303)


def photo_response(db: Session, pid: int, thumb: bool) -> Response:
    col = CarePhoto.thumb if thumb else CarePhoto.data
    data = db.execute(select(col).where(CarePhoto.id == pid)).scalar_one_or_none()
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/care/photo/{pid}.jpg")
def photo_img(pid: int, s: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    _own(db, CarePhoto, pid, teacher.academy_id)
    return photo_response(db, pid, s == "t")


# ── 월간 리포트 ──────────────────────────────────────────────────────────────


def _batch(db: Session, r: CareReport) -> list[CareReport]:
    q = select(CareReport).options(selectinload(CareReport.student)).where(CareReport.academy_id == r.academy_id, CareReport.start == r.start, CareReport.end == r.end)
    q = q.where(CareReport.class_id == r.class_id) if r.class_id else q.where(CareReport.id == r.id)
    return sorted(db.execute(q).scalars(), key=lambda x: x.student.name)


@router.get("/care/reports")
def reports(request: Request, ym: str = "", db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    start, end = care.month_range(ym)
    items = list(db.execute(select(CareReport).options(selectinload(CareReport.student), selectinload(CareReport.class_group))
                            .where(CareReport.academy_id == aid, CareReport.start <= end, CareReport.end >= start)
                            .order_by(CareReport.start.desc(), CareReport.class_id, CareReport.id)).scalars())
    groups: dict[tuple, list[CareReport]] = {}
    for r in items:
        groups.setdefault((r.class_id, r.start, r.end, r.class_group.name if r.class_group else "반 없음"), []).append(r)
    for v in groups.values():
        v.sort(key=lambda x: x.student.name)
    prev_m = (start - timedelta(days=1)).strftime("%Y-%m")
    next_m = (end + timedelta(days=1)).strftime("%Y-%m")
    return render(request, "care/reports.html", {"teacher": teacher, "active": "care", "tab": "reports", "groups": groups, "start": start, "end": end,
                                                 "ym": start.strftime("%Y-%m"), "prev_m": prev_m, "next_m": next_m, "classes": _classes(db, aid),
                                                 "today": care.today()})


@router.post("/care/reports/new")
def report_new(request: Request, class_id: str = Form(...), start: str = Form(...), end: str = Form(...), title: str = Form(""),
               db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    aid = teacher.academy_id
    c = _own(db, ClassGroup, int(class_id), aid) if class_id.isdigit() else None
    if c is None:
        raise HTTPException(400, "반을 골라 주세요.")
    d1, d2 = care.parse_day(start), care.parse_day(end)
    if d2 < d1:
        d1, d2 = d2, d1
    title = title.strip()[:80] or (f"{d1.year}년 {d1.month}월 학습 리포트" if d1.day == 1 and (d2 + timedelta(days=1)).day == 1 and d1.month == d2.month
                                   else f"{d1.month}/{d1.day}~{d2.month}/{d2.day} 학습 리포트")
    have = {r.student_id for r in db.execute(select(CareReport).where(CareReport.class_id == c.id, CareReport.start == d1, CareReport.end == d2)).scalars()}
    made = []
    for s in _students(db, aid, c.id):
        if s.id not in have:
            r = CareReport(academy_id=aid, student_id=s.id, class_id=c.id, title=title, start=d1, end=d2)
            db.add(r)
            made.append(r)
    db.commit()
    if not made and not have:
        flash(request, f"{c.name}에 학생이 없어요.", "error")
        return RedirectResponse("/care/reports", status_code=303)
    flash(request, f"{c.name} 학생 {len(made)}명의 '{title}'를 만들었어요." + (f" (이미 있던 {len(have)}명은 그대로)" if have else "") +
          " 학생마다 코멘트를 적고 공개해 주세요.")
    first = made[0] if made else db.execute(select(CareReport).where(CareReport.class_id == c.id, CareReport.start == d1, CareReport.end == d2)).scalars().first()
    return RedirectResponse(f"/care/reports/{first.id}", status_code=303)


@router.get("/care/reports/{rid}")
def report_edit(rid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    r = _own(db, CareReport, rid, teacher.academy_id)
    batch = _batch(db, r)
    i = next((k for k, x in enumerate(batch) if x.id == r.id), 0)
    data = care.report_data(db, r.student, r.start, r.end)
    return render(request, "care/report_edit.html", {"teacher": teacher, "active": "care", "tab": "reports", "r": r, "batch": batch,
                                                     "prev": batch[i - 1] if i > 0 else None, "next": batch[i + 1] if i + 1 < len(batch) else None,
                                                     "d": data, "has_key": bool(teacher.gemini_key_enc)})


def _notify_reports(db: Session, rs: list[CareReport]) -> int:
    msgs = []
    for r in rs:
        s = r.student
        msgs.append({"student_id": s.id, "title": f"📊 {s.name} {r.title}", "body": (r.summary or "새 학습 리포트가 올라왔어요. 눌러서 확인해 주세요.")[:200],
                     "url": f"/p/{care.ensure_token(db, s)}/r/{r.id}"})
        r.notified_at = now()
    db.commit()
    if msgs:
        push.deliver_later(rs[0].academy_id, msgs, "report")
    from ..models import ParentPushSub
    from sqlalchemy import func
    return sum(1 for (_, n) in db.execute(select(ParentPushSub.student_id, func.count()).where(ParentPushSub.student_id.in_([r.student_id for r in rs]))
                                          .group_by(ParentPushSub.student_id)).all() if n)


@router.post("/care/reports/{rid}")
async def report_save(rid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    r = _own(db, CareReport, rid, teacher.academy_id)
    form = await request.form()
    g = lambda k: str(form.get(k, "") or "").strip()  # noqa: E731
    r.title = g("title")[:80] or r.title
    r.grade, r.summary = g("grade")[:10], g("summary")[:300]
    r.strength, r.goal, r.message = g("strength"), g("goal"), g("message")
    action = g("action")
    msg = "저장했어요."
    if action in ("publish", "publish_notify"):
        r.status, r.published_at = "public", r.published_at or now()
        msg = "공개했어요. 학부모 화면에 리포트가 보여요."
    elif action == "unpublish":
        r.status = "draft"
        msg = "비공개로 돌렸어요. 학부모 화면에서 안 보여요."
    db.commit()
    if action == "publish_notify":
        on = _notify_reports(db, [r])
        msg += " 알림을 보내는 중이에요." if on else " (이 학부모님은 아직 앱 알림을 안 켜셨어요 — '학부모 앱 연결'에서 링크를 보내 주세요)"
    flash(request, msg)
    nxt = g("go")
    return RedirectResponse(f"/care/reports/{int(nxt)}" if nxt.isdigit() else f"/care/reports/{r.id}", status_code=303)


@router.post("/care/reports/{rid}/batch")
def report_batch(rid: int, request: Request, action: str = Form(...), db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    """같은 반·같은 기간 리포트 한꺼번에: 공개+알림 / 지우기."""
    r = _own(db, CareReport, rid, teacher.academy_id)
    batch = _batch(db, r)
    if action == "publish_notify":
        todo = [x for x in batch if x.status != "public" or not x.notified_at]
        for x in todo:
            x.status, x.published_at = "public", x.published_at or now()
        db.commit()
        on = _notify_reports(db, todo) if todo else 0
        flash(request, f"리포트 {len(todo)}개를 공개했어요." + (f" 앱 알림을 켠 학부모 {on}명에게 알림을 보내는 중이에요." if on else ""))
    elif action == "delete":
        for x in batch:
            db.delete(x)
        db.commit()
        flash(request, f"리포트 {len(batch)}개를 지웠어요. (수업 기록은 그대로예요)")
        return RedirectResponse("/care/reports", status_code=303)
    return RedirectResponse(f"/care/reports?ym={r.start.strftime('%Y-%m')}", status_code=303)


REPORT_SYSTEM = """당신은 영어 학원 선생님을 도와 학부모에게 보내는 월간 학습 리포트의 코멘트를 씁니다. JSON 으로만 답합니다.
형식: {"grade": "총평 등급(아주 좋음/좋음/보통/노력 필요 중 하나)", "summary": "총평 한 줄(40자 안팎)", "strength": "잘한 점 2~3문장", "goal": "다음 달 목표 2~3문장", "message": "선생님 한마디 2~3문장"}
- 학부모님께 쓰는 존댓말(해요체), 따뜻하지만 과장하지 않습니다. 기록에 없는 일은 지어내지 않습니다.
- 출석률·숙제 완료율·시험 점수(반 평균과 비교)·진도 같은 숫자를 근거로 구체적으로 씁니다. 점수가 떨어졌으면 부드럽게 짚고 해결책을 제안합니다.
- 선생님 메모(날짜별 코멘트)에 나온 내용을 활용합니다. 학생 이름은 '○○이는'처럼 자연스럽게 부릅니다.
- 이미 선생님이 적어 둔 칸이 있으면 그 내용을 살려 다듬습니다."""


@router.post("/care/reports/{rid}/ai")
async def report_ai(rid: int, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    r = _own(db, CareReport, rid, teacher.academy_id)
    key = decrypt(teacher.gemini_key_enc)
    if not key:
        return JSONResponse({"error": "Gemini API 키가 없어요. 설정 화면에서 키를 먼저 넣어 주세요."}, status_code=400)
    d = care.report_data(db, r.student, r.start, r.end)
    fmt = care.fmt_num
    lines = [f"학생: {r.student.name}", f"기간: {r.start} ~ {r.end} (수업 {d['lessons']}번)",
             f"출결: 출석 {d['att']['출']} · 지각 {d['att']['지']} · 결석 {d['att']['결']} (출석률 {d['attend_rate'] if d['attend_rate'] is not None else '-'}%)",
             f"숙제 완료율: {d['hw_rate'] if d['hw_rate'] is not None else '기록 없음'}%" if d['hw_rate'] is not None else "숙제: 기록 없음"]
    if d["tests"]:
        lines.append("시험:")
        lines += [f"- {t['label']} {t['test_name']}: {fmt(t['score'])}/{fmt(t['total'])} (100점 환산 {t['pct']}"
                  + (f", 반 평균 {t['class_avg']}" if t["class_avg"] is not None else "") + ")" for t in d["tests"]]
    for p in d["progress"]:
        lines.append(f"진도 {p['book'].title}: {', '.join(p['units'][:12])}" + (f" p.{p['pages'][0]}~{p['pages'][1]}" if p["pages"] else ""))
    for f in d["free"][:10]:
        lines.append(f"진도 {f['label']}: {f['text']}")
    notes = [f"- {x['label']}: {x['comment']}" for x in d["recs"] if x["comment"]]
    if notes:
        lines += ["선생님 날짜별 코멘트:"] + notes[:20]
    mine = {k: getattr(r, k) for k in ("summary", "strength", "goal", "message") if getattr(r, k)}
    if mine:
        lines.append("선생님이 이미 적어 둔 칸: " + str(mine))
    try:
        res = await asyncio.to_thread(llm.generate, key, "\n".join(lines), system=REPORT_SYSTEM, model=teacher.gemini_model or DEFAULT_GEMINI_MODEL,
                                      json_mode=True, temperature=0.6, max_tokens=3000)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if not isinstance(res, dict):
        return JSONResponse({"error": "AI 답을 읽지 못했어요. 다시 눌러 주세요."}, status_code=502)
    return {k: str(res.get(k) or "").strip() for k in ("grade", "summary", "strength", "goal", "message")}


@router.get("/care/reports/{rid}/view")
def report_view_teacher(rid: int, request: Request, db: Session = Depends(get_db), teacher: Teacher = Depends(current_teacher)):
    r = _own(db, CareReport, rid, teacher.academy_id)
    return render_report(request, db, r, teacher_view=True)


def render_report(request: Request, db: Session, r: CareReport, teacher_view: bool, token: str = ""):
    d = care.report_data(db, r.student, r.start, r.end)
    return render(request, "parent/report.html", {"r": r, "student": r.student, "academy": db.get(Academy, r.academy_id), "d": d,
                                                  "teacher_view": teacher_view, "token": token, "fmt": care.fmt_num, "ATTEND": care.ATTEND})
