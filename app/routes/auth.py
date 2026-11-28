import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Academy, Teacher
from ..security import encrypt, hash_pin, make_code, verify_pin
from ..web import flash, render

router = APIRouter()

LOGIN_ID_RE = re.compile(r"^[a-zA-Z0-9_]{3,40}$")


def _go(url: str):
    return RedirectResponse(url, status_code=303)


@router.get("/login")
def login_page(request: Request):
    if request.session.get("teacher_id"):
        return _go("/")
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, login_id: str = Form(...), pin: str = Form(...), db: Session = Depends(get_db)):
    teacher = db.execute(select(Teacher).where(Teacher.login_id == login_id.strip().lower())).scalar_one_or_none()
    if teacher is None or not verify_pin(pin, teacher.pin_hash):
        return render(request, "login.html", {"error": "아이디 또는 PIN이 맞지 않습니다.", "login_id": login_id}, 400)
    request.session.clear()
    request.session["teacher_id"] = teacher.id
    return _go("/")


@router.get("/join/{code}")
def join_page(code: str, request: Request, db: Session = Depends(get_db)):
    """선생님 초대 링크: 그 학원 선생님으로 가입 → 바로 학원 이름 앱 설치 안내."""
    academy = db.execute(select(Academy).where(Academy.invite_code == code.strip().upper())).scalar_one_or_none()
    if academy is None:
        return render(request, "signup.html", {"error": "초대 링크가 바뀌었거나 잘못됐어요. 원장님께 새 링크를 받아 주세요.", "form": {"mode": "join"}}, 404)
    tid = request.session.get("teacher_id")
    if tid:
        t = db.get(Teacher, tid)
        if t and t.academy_id == academy.id:
            return _go("/?app=1")  # 이미 가입한 선생님: 앱 설치 안내만
    return render(request, "join.html", {"academy": academy, "pwa_academy": academy, "form": {"invite_code": academy.invite_code, "mode": "join"}})


@router.get("/signup")
def signup_page(request: Request, code: str = ""):
    return render(request, "signup.html", {"form": {"invite_code": code, "mode": "join" if code else "new"}})


@router.post("/signup")
def signup(
    request: Request,
    mode: str = Form("new"),
    academy_name: str = Form(""),
    invite_code: str = Form(""),
    name: str = Form(...),
    login_id: str = Form(...),
    pin: str = Form(...),
    pin2: str = Form(...),
    gemini_key: str = Form(""),
    db: Session = Depends(get_db),
):
    form = dict(mode=mode, academy_name=academy_name, invite_code=invite_code, name=name, login_id=login_id)
    login_id = login_id.strip().lower()

    def fail(msg):
        if mode == "join" and request.headers.get("referer", "").find("/join/") >= 0:
            ac = db.execute(select(Academy).where(Academy.invite_code == invite_code.strip().upper())).scalar_one_or_none()
            if ac is not None:
                return render(request, "join.html", {"error": msg, "academy": ac, "pwa_academy": ac, "form": form}, 400)
        return render(request, "signup.html", {"error": msg, "form": form}, 400)

    if not LOGIN_ID_RE.match(login_id):
        return fail("아이디는 영문·숫자·밑줄(_)로 3자 이상 써 주세요.")
    if not name.strip():
        return fail("이름을 써 주세요.")
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        return fail("PIN은 숫자 4~8자리로 정해 주세요.")
    if pin != pin2:
        return fail("PIN 확인이 맞지 않습니다.")
    if db.execute(select(Teacher).where(Teacher.login_id == login_id)).scalar_one_or_none():
        return fail("이미 쓰고 있는 아이디입니다.")

    if mode == "join":
        academy = db.execute(
            select(Academy).where(Academy.invite_code == invite_code.strip().upper())
        ).scalar_one_or_none()
        if academy is None:
            return fail("학원 초대 코드를 찾을 수 없습니다. 원장님께 코드를 다시 확인해 주세요.")
        role = "teacher"
    else:
        if not academy_name.strip():
            return fail("학원 이름을 써 주세요.")
        code = make_code(8)
        while db.execute(select(Academy).where(Academy.invite_code == code)).scalar_one_or_none():
            code = make_code(8)
        academy = Academy(name=academy_name.strip(), invite_code=code)
        db.add(academy)
        db.flush()
        role = "owner"

    teacher = Teacher(
        academy_id=academy.id,
        login_id=login_id,
        name=name.strip(),
        pin_hash=hash_pin(pin),
        role=role,
        gemini_key_enc=encrypt(gemini_key.strip()) if gemini_key.strip() else None,
        perms=["care"] if role == "teacher" else None,  # 새로 합류한 선생님은 학습관리만 (원장이 설정에서 더 열어 줌)
    )
    db.add(teacher)
    db.commit()
    request.session.clear()
    request.session["teacher_id"] = teacher.id
    flash(request, f"{academy.name}에 오신 것을 환영합니다." + ("" if gemini_key.strip() else " 설정에서 Gemini 키를 넣어야 자료 만들기를 쓸 수 있어요.")
          + (" 아래 '앱 설치하기'를 누르면 휴대폰 홈 화면에 학원 앱이 생겨요." if mode == "join" else ""))
    return _go("/?app=1" if mode == "join" else "/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _go("/login")
