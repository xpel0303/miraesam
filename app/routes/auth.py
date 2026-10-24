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
    )
    db.add(teacher)
    db.commit()
    request.session.clear()
    request.session["teacher_id"] = teacher.id
    flash(request, f"{academy.name}에 오신 것을 환영합니다." + ("" if gemini_key.strip() else " 설정에서 Gemini 키를 넣어야 자료 만들기를 쓸 수 있어요."))
    return _go("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _go("/login")
