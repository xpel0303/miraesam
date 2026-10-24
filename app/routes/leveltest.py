"""레벨테스트.

선생님 (/leveltest)   시험지 만들기·고치기(문항 데이터·듣기 파일·그림), 공용 링크, 응시 결과·리포트
재원생 (/s/{code})    학생 화면에서 레벨테스트를 골라 응시 → 이름·반이 자동으로 붙음
신규생 (/lt/{token})  공용 링크: 이름·학교/학년만 적고 응시 (상담 오기 전에 미리)
응시 (/lt/r/{key})    한 번의 응시. 자동 저장, 제한 시간, 제출하면 서버가 채점해 리포트를 저장
정답은 학생 화면에 보내지 않습니다.
"""
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.requests import ClientDisconnect
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..leveltest import spec as lspec
from ..models import Academy, LevelResult, LevelTest, StoredFile, Student, Teacher
from ..web import current_teacher, flash, render

router = APIRouter()
GRACE = 60
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg", ".aac": "audio/aac"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}
MAX_AUDIO = 60 * 1024 * 1024


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _now():
    return datetime.now(timezone.utc)


def _own(db: Session, tid: int, teacher: Teacher) -> LevelTest:
    t = db.get(LevelTest, tid)
    if t is None or t.academy_id != teacher.academy_id:
        raise HTTPException(404, "레벨테스트를 찾을 수 없어요.")
    return t


def _token(db: Session, academy: Academy) -> str:
    if not academy.lt_token:
        academy.lt_token = secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")[:8]
        db.commit()
    return academy.lt_token


def _tests(db: Session, academy_id: int, active_only=True) -> list[LevelTest]:
    q = select(LevelTest).where(LevelTest.academy_id == academy_id)
    if active_only:
        q = q.where(LevelTest.active.is_(True))
    return list(db.execute(q.order_by(LevelTest.sort, LevelTest.id)).scalars())


def _n_items(t: LevelTest) -> int:
    return len(lspec.items(t.spec or {}))


def _viewer_teacher(request: Request, db: Session, academy_id: int) -> bool:
    tid = request.session.get("teacher_id")
    tch = db.get(Teacher, tid) if tid else None
    return bool(tch and tch.academy_id == academy_id)


# ================================================================ 선생님

@router.get("/leveltest")
def index(request: Request, test_id: str = "", q: str = "", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    academy = db.get(Academy, teacher.academy_id)
    tests = _tests(db, teacher.academy_id, active_only=False)
    rq = (select(LevelResult).options(selectinload(LevelResult.test), selectinload(LevelResult.student))
          .where(LevelResult.academy_id == teacher.academy_id))
    tid = int(test_id) if test_id.isdigit() else None
    if tid:
        rq = rq.where(LevelResult.test_id == tid)
    if q.strip():
        rq = rq.where(LevelResult.name.ilike(f"%{q.strip()[:40]}%"))
    results = list(db.execute(rq.order_by(LevelResult.started_at.desc()).limit(300)).scalars())
    counts = dict(db.execute(select(LevelResult.test_id, func.count()).where(LevelResult.academy_id == teacher.academy_id,
                                                                            LevelResult.submitted_at.is_not(None)).group_by(LevelResult.test_id)).all())
    return render(request, "leveltest/index.html", {"teacher": teacher, "tests": tests, "results": results, "counts": counts,
                                                    "link": f"{str(request.base_url).rstrip('/')}/lt/{_token(db, academy)}",
                                                    "n_items": {t.id: _n_items(t) for t in tests}, "test_id": tid, "q": q, "active": "leveltest"})


@router.post("/leveltest/link/reset")
def reset_link(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    academy = db.get(Academy, teacher.academy_id)
    academy.lt_token = None
    _token(db, academy)
    flash(request, "공용 링크를 새로 만들었어요. 예전 링크로는 이제 들어올 수 없어요.")
    return _go("/leveltest")


@router.post("/leveltest/new")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    title = str(f.get("title", "")).strip()[:80] or "새 레벨테스트"
    n = db.execute(select(func.count()).select_from(LevelTest).where(LevelTest.academy_id == teacher.academy_id)).scalar() or 0
    t = LevelTest(academy_id=teacher.academy_id, title=title, sort=n + 1, active=False, spec={}, images={})
    db.add(t)
    db.commit()
    return _go(f"/leveltest/{t.id}")


@router.get("/leveltest/{tid}")
def edit(tid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    t = _own(db, tid, teacher)
    return _edit_page(request, teacher, db, t, json.dumps(t.spec, ensure_ascii=False, indent=1) if t.spec else "", [])


def _edit_page(request, teacher, db, t, spec_text, errors, status=200):
    need = lspec.image_keys(t.spec or {})
    audio = db.get(StoredFile, t.audio_file_id) if t.audio_file_id else None
    return render(request, "leveltest/edit.html", {
        "teacher": teacher, "t": t, "spec_text": spec_text, "errors": errors, "n_items": _n_items(t),
        "sections": [(s["title"], sum(len(g["questions"]) for g in s["groups"]), s.get("audio")) for s in (t.spec or {}).get("sections", [])],
        "missing": sorted(need - set(t.images or {})), "extra": sorted(set(t.images or {}) - need), "audio": audio,
        "needs_audio": any(s.get("audio") for s in (t.spec or {}).get("sections", [])), "active": "leveltest"}, status)


@router.post("/leveltest/{tid}")
async def save(tid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    t = _own(db, tid, teacher)
    f = await request.form()
    t.title = str(f.get("title", "")).strip()[:80] or t.title
    t.sub = str(f.get("sub", "")).strip()[:120]
    t.sort = int(f.get("sort")) if str(f.get("sort", "")).strip().lstrip("-").isdigit() else t.sort
    m = str(f.get("minutes", "")).strip()
    t.minutes = max(0, min(300, int(m))) if m.isdigit() else 0
    t.show_result = f.get("show_result") == "1"
    want_active = f.get("active") == "1"
    raw = str(f.get("spec", "")).strip()
    errors = []
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            errors = [f"문항 데이터 형식이 깨졌어요 ({e.lineno}번째 줄 근처). 따옴표·쉼표를 확인해 주세요."]
        else:
            cleaned, errors = lspec.check(data)
            if not errors:
                if "minutes" in cleaned and not m:
                    t.minutes = cleaned.pop("minutes")
                cleaned.pop("minutes", None)
                t.spec = cleaned
    elif t.spec:
        t.spec = {}
    if errors:
        db.commit()  # 제목 등 다른 칸은 저장
        return _edit_page(request, teacher, db, t, raw, errors, 400)
    if want_active and not _n_items(t):
        want_active = False
        flash(request, "문항이 없어서 아직 학생에게 열지 않았어요.", "error")
    t.active = want_active
    db.commit()
    flash(request, f"저장했어요. 문항 {_n_items(t)}개." + (" 학생 화면에 보여요." if t.active else " (아직 학생에게 안 보여요)"))
    return _go(f"/leveltest/{tid}")


@router.post("/leveltest/{tid}/audio")
async def upload_audio(tid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    t = _own(db, tid, teacher)
    f = await request.form()
    u = f.get("audio")
    if not getattr(u, "filename", ""):
        flash(request, "듣기 파일을 골라 주세요.", "error")
        return _go(f"/leveltest/{tid}#files")
    ext = "." + u.filename.rsplit(".", 1)[-1].lower() if "." in u.filename else ""
    data = await u.read()
    if ext not in AUDIO_TYPES:
        flash(request, "mp3·m4a·wav 파일만 올릴 수 있어요.", "error")
    elif len(data) > MAX_AUDIO:
        flash(request, f"파일이 너무 커요 ({len(data) // (1024 * 1024)}MB). {MAX_AUDIO // (1024 * 1024)}MB까지 돼요.", "error")
    else:
        old = t.audio_file_id
        sf = StoredFile(academy_id=t.academy_id, filename=u.filename[:200], mime=AUDIO_TYPES[ext], data=data)
        db.add(sf)
        db.flush()
        t.audio_file_id = sf.id
        if old:
            db.execute(delete(StoredFile).where(StoredFile.id == old))
        db.commit()
        flash(request, f"듣기 파일 '{u.filename}'을 올렸어요.")
    return _go(f"/leveltest/{tid}#files")


@router.post("/leveltest/{tid}/images")
async def upload_images(tid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """파일 이름(확장자 뺀 것)이 문항 데이터의 그림 이름. 예: kitchen.png → image: 'kitchen'"""
    t = _own(db, tid, teacher)
    f = await request.form()
    images, added, bad = dict(t.images or {}), [], []
    for u in f.getlist("images"):
        if not getattr(u, "filename", ""):
            continue
        name, _, ext = u.filename.rpartition(".")
        ext = "." + ext.lower()
        if ext not in IMAGE_TYPES or not name:
            bad.append(u.filename)
            continue
        data = await u.read()
        if len(data) > 5 * 1024 * 1024:
            bad.append(u.filename)
            continue
        key = re.sub(r"\s+", "_", name.strip())[:60]
        sf = StoredFile(academy_id=t.academy_id, filename=u.filename[:200], mime=IMAGE_TYPES[ext], data=data)
        db.add(sf)
        db.flush()
        if key in images:
            db.execute(delete(StoredFile).where(StoredFile.id == images[key]))
        images[key] = sf.id
        added.append(key)
    t.images = images
    db.commit()
    msg = f"그림 {len(added)}개를 올렸어요." if added else "올린 그림이 없어요."
    if bad:
        msg += f" 못 올린 파일: {', '.join(bad[:5])} (png·jpg·webp, 5MB까지)"
    flash(request, msg, "error" if bad and not added else "ok")
    return _go(f"/leveltest/{tid}#files")


@router.post("/leveltest/{tid}/images/{key}/delete")
def delete_image(tid: int, key: str, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    t = _own(db, tid, teacher)
    images = dict(t.images or {})
    fid = images.pop(key, None)
    if fid:
        db.execute(delete(StoredFile).where(StoredFile.id == fid))
    t.images = images
    db.commit()
    return _go(f"/leveltest/{tid}#files")


@router.post("/leveltest/{tid}/delete")
def remove(tid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    t = _own(db, tid, teacher)
    n = db.execute(select(func.count()).select_from(LevelResult).where(LevelResult.test_id == t.id)).scalar() or 0
    ids = [x for x in [t.audio_file_id, *(t.images or {}).values()] if x]
    db.execute(delete(LevelResult).where(LevelResult.test_id == t.id))
    db.delete(t)
    db.flush()
    if ids:
        db.execute(delete(StoredFile).where(StoredFile.id.in_(ids)))
    db.commit()
    flash(request, f"'{t.title}' 레벨테스트를 지웠어요." + (f" 응시 결과 {n}개도 같이 지웠어요." if n else ""))
    return _go("/leveltest")


@router.get("/leveltest/{tid}/preview")
def preview(tid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    t = _own(db, tid, teacher)
    if not _n_items(t):
        raise HTTPException(404, "문항이 아직 없어요.")
    return render(request, "lt/take.html", {"t": t, "spec": lspec.public(t.spec), "answers": {}, "left": t.minutes * 60 if t.minutes else None,
                                            "r": None, "preview": True, **_shell(db, t.academy_id, "미리 보기")})


@router.get("/leveltest/r/{rid}")
def result(rid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    r = db.get(LevelResult, rid)
    if r is None or r.academy_id != teacher.academy_id:
        raise HTTPException(404, "결과를 찾을 수 없어요.")
    rep = r.report or (lspec.report(r.test.spec or {}, r.answers or {}) if r.test else None)
    return render(request, "leveltest/result.html", {"teacher": teacher, "r": r, "rep": rep, "active": "leveltest",
                                                     "academy": db.get(Academy, teacher.academy_id)})


@router.post("/leveltest/r/{rid}/delete")
def delete_result(rid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    r = db.get(LevelResult, rid)
    if r is None or r.academy_id != teacher.academy_id:
        raise HTTPException(404)
    db.delete(r)
    db.commit()
    flash(request, f"{r.name} 학생의 응시 결과를 지웠어요.")
    return _go("/leveltest")


# ================================================================ 학생 · 신규생

def _shell(db: Session, academy_id: int, name: str, code: str = "") -> dict:
    """student/_base.html 머리말에 필요한 값."""
    return {"student": {"name": name}, "academy": db.get(Academy, academy_id), "code": code}


def _new_result(db: Session, t: LevelTest, name: str, info: str, student: Student | None = None) -> LevelResult:
    r = LevelResult(academy_id=t.academy_id, test_id=t.id, student_id=student.id if student else None, key=secrets.token_urlsafe(12)[:16],
                    name=name, info=info, answers={})
    db.add(r)
    db.commit()
    return r


@router.get("/lt/{token}")
def public_enter(token: str, request: Request, db: Session = Depends(get_db)):
    academy = db.execute(select(Academy).where(Academy.lt_token == token)).scalar_one_or_none()
    if academy is None:
        raise HTTPException(404, "레벨테스트 링크가 바뀌었거나 없는 주소예요. 학원에 새 링크를 물어봐 주세요.")
    return render(request, "lt/enter.html", {"tests": _tests(db, academy.id), "token": token, "form": {}, **_shell(db, academy.id, "레벨테스트")})


@router.post("/lt/{token}")
async def public_start(token: str, request: Request, db: Session = Depends(get_db)):
    academy = db.execute(select(Academy).where(Academy.lt_token == token)).scalar_one_or_none()
    if academy is None:
        raise HTTPException(404)
    f = await request.form()
    name, info = str(f.get("name", "")).strip()[:40], str(f.get("info", "")).strip()[:80]
    tid = int(f["test_id"]) if str(f.get("test_id", "")).isdigit() else 0
    t = next((x for x in _tests(db, academy.id) if x.id == tid), None)
    errors = []
    if not name:
        errors.append("이름을 적어 주세요.")
    if t is None:
        errors.append("볼 시험을 골라 주세요.")
    if errors:
        return render(request, "lt/enter.html", {"tests": _tests(db, academy.id), "token": token, "errors": errors,
                                                 "form": {"name": name, "info": info, "test_id": tid}, **_shell(db, academy.id, "레벨테스트")}, 400)
    r = _new_result(db, t, name, info)
    return _go(f"/lt/r/{r.key}")


@router.post("/s/{code}/lt/{tid}")
def student_start(code: str, tid: int, db: Session = Depends(get_db)):
    s = db.execute(select(Student).options(selectinload(Student.class_group), selectinload(Student.school))
                   .where(Student.access_code == code.upper(), Student.active.is_(True))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404)
    t = next((x for x in _tests(db, s.academy_id) if x.id == tid), None)
    if t is None:
        raise HTTPException(404, "이 레벨테스트는 지금 볼 수 없어요.")
    # 이미 본 시험은 결과로, 풀다 만 것은 이어서 (다시 보게 하려면 선생님이 결과를 지우면 됨)
    r = db.execute(select(LevelResult).where(LevelResult.test_id == t.id, LevelResult.student_id == s.id)
                   .order_by(LevelResult.id.desc())).scalars().first()
    if r is None:
        info = " ".join(x for x in [s.school.name if s.school else "", s.class_group.name if s.class_group else ""] if x)
        r = _new_result(db, t, s.name, info, s)
    return _go(f"/lt/r/{r.key}")


def _result(db: Session, key: str) -> LevelResult:
    r = db.execute(select(LevelResult).options(selectinload(LevelResult.test), selectinload(LevelResult.student))
                   .where(LevelResult.key == key)).scalar_one_or_none()
    if r is None or r.test is None:
        raise HTTPException(404, "응시 기록을 찾을 수 없어요.")
    return r


def _deadline(r: LevelResult):
    return _utc(r.started_at) + timedelta(minutes=r.test.minutes) if r.test.minutes else None


def _code(r: LevelResult) -> str:
    return r.student.access_code if r.student else ""


@router.get("/lt/r/{key}")
def take(key: str, request: Request, db: Session = Depends(get_db)):
    r = _result(db, key)
    t = r.test
    shell = _shell(db, r.academy_id, r.name, _code(r))
    if r.submitted_at is None:
        dl = _deadline(r)
        if dl is not None and _now() > dl + timedelta(seconds=GRACE):  # 시간이 지나 창을 닫았던 경우: 저장된 답으로 제출
            _finish(db, r, r.answers or {})
        elif not t.active:
            raise HTTPException(404, "이 레벨테스트는 지금 닫혀 있어요. 학원에 물어봐 주세요.")
        else:
            return render(request, "lt/take.html", {"t": t, "spec": lspec.public(t.spec), "answers": r.answers or {}, "r": r, "preview": False,
                                                    "left": None if dl is None else max(0, int((dl - _now()).total_seconds())), **shell})
    return render(request, "lt/done.html", {"t": t, "r": r, "rep": r.report if t.show_result else None, **shell})


@router.post("/lt/r/{key}/save")
async def save_answers(key: str, request: Request, db: Session = Depends(get_db)):
    r = _result(db, key)
    if r.submitted_at is not None:
        return JSONResponse({"ok": False, "reason": "closed"}, status_code=409)
    dl = _deadline(r)
    if dl is not None and _now() > dl + timedelta(seconds=GRACE):
        return JSONResponse({"ok": False, "reason": "time"}, status_code=409)
    try:
        body = await request.json()
    except (ValueError, ClientDisconnect):  # 새로고침 중 끊긴 저장은 그냥 무시
        return JSONResponse({"ok": False}, status_code=400)
    r.answers = lspec.clean_answers(r.test.spec or {}, body.get("answers") if isinstance(body, dict) else None)
    db.commit()
    return {"ok": True, "left": None if dl is None else max(0, int((dl - _now()).total_seconds()))}


def _finish(db: Session, r: LevelResult, answers: dict):
    rep = lspec.report(r.test.spec or {}, answers)
    rep["test"] = r.test.title
    r.answers, r.report, r.score, r.level, r.submitted_at = answers, rep, rep["score"], rep["band"]["label"], _now()
    db.commit()


@router.post("/lt/r/{key}/submit")
async def submit(key: str, request: Request, db: Session = Depends(get_db)):
    r = _result(db, key)
    if r.submitted_at is None:
        f = await request.form()
        try:
            raw = json.loads(str(f.get("answers") or "{}"))
        except json.JSONDecodeError:
            raw = {}
        dl = _deadline(r)
        late = dl is not None and _now() > dl + timedelta(seconds=GRACE)
        _finish(db, r, (r.answers or {}) if late else lspec.clean_answers(r.test.spec or {}, raw))
    return _go(f"/lt/r/{key}")


# ---------------- 듣기 파일·그림 (열린 시험이거나, 같은 학원 선생님일 때만) ----------------

def _open_test(request: Request, db: Session, tid: int) -> LevelTest:
    t = db.get(LevelTest, tid)
    if t is None or not (t.active or _viewer_teacher(request, db, t.academy_id)):
        raise HTTPException(404)
    return t


@router.get("/lt/a/{tid}")
def audio(tid: int, request: Request, db: Session = Depends(get_db)):
    t = _open_test(request, db, tid)
    sf = db.get(StoredFile, t.audio_file_id) if t.audio_file_id else None
    if sf is None:
        raise HTTPException(404)
    data, size = sf.data, len(sf.data)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=86400"}
    rng = request.headers.get("range", "")
    m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
    if m and (m.group(1) or m.group(2)):  # 아이폰 사파리는 이렇게 조금씩 받아야 재생됨
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        else:
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(data[start:end + 1], status_code=206, media_type=sf.mime, headers=headers)
    return Response(data, media_type=sf.mime, headers=headers)


@router.get("/lt/f/{tid}/{name}")
def image(tid: int, name: str, request: Request, db: Session = Depends(get_db)):
    t = _open_test(request, db, tid)
    fid = (t.images or {}).get(name)
    sf = db.get(StoredFile, fid) if fid else None
    if sf is None:
        raise HTTPException(404)
    return Response(sf.data, media_type=sf.mime, headers={"Cache-Control": "private, max-age=86400"})
