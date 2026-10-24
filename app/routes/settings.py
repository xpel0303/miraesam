import base64
import re

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DEFAULT_GEMINI_MODEL, GEMINI_MODELS
from ..db import get_db
from ..jobs import enqueue
from ..models import Academy, Teacher
from ..security import decrypt, encrypt, hash_pin, make_code, mask_key, verify_pin
from ..web import current_teacher, flash, owner_only, render

router = APIRouter(prefix="/settings")

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_MAX = 300 * 1024


def _back(anchor: str = ""):
    return RedirectResponse(f"/settings{anchor}", status_code=303)


@router.get("")
def settings_page(request: Request, job: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    academy = db.get(Academy, teacher.academy_id)
    teachers = list(db.execute(select(Teacher).where(Teacher.academy_id == academy.id).order_by(Teacher.id)).scalars())
    return render(
        request,
        "settings.html",
        {
            "teacher": teacher,
            "academy": academy,
            "teachers": teachers,
            "masked_key": mask_key(decrypt(teacher.gemini_key_enc)),
            "models": GEMINI_MODELS,
            "current_model": teacher.gemini_model or DEFAULT_GEMINI_MODEL,
            "test_job": job,
            "active": "settings",
        },
    )


@router.post("/gemini")
def save_gemini(
    request: Request,
    gemini_key: str = Form(""),
    gemini_model: str = Form(DEFAULT_GEMINI_MODEL),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    if gemini_key.strip():
        teacher.gemini_key_enc = encrypt(gemini_key.strip())
    if gemini_model in GEMINI_MODELS:
        teacher.gemini_model = gemini_model
    db.commit()
    flash(request, "Gemini 설정을 저장했습니다.")
    return _back("#gemini")


@router.post("/gemini/test")
def test_gemini(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    if not teacher.gemini_key_enc:
        flash(request, "먼저 Gemini 키를 저장해 주세요.", "error")
        return _back("#gemini")
    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="gemini_test", title="Gemini 연결 확인", payload={})
    return RedirectResponse(f"/settings?job={job.id}#gemini", status_code=303)


@router.post("/academy")
async def save_academy(
    request: Request,
    name: str = Form(...),
    brand_color: str = Form("#3a3a3a"),
    remove_logo: str = Form(""),
    logo: UploadFile | None = File(None),
    teacher: Teacher = Depends(owner_only),
    db: Session = Depends(get_db),
):
    academy = db.get(Academy, teacher.academy_id)
    if name.strip():
        academy.name = name.strip()[:100]
    if HEX_RE.match(brand_color):
        academy.brand_color = brand_color
    if remove_logo:
        academy.logo = None
    elif logo is not None and logo.filename:
        data = await logo.read()
        if len(data) > LOGO_MAX:
            flash(request, "로고 파일은 300KB 이하로 올려 주세요.", "error")
            return _back("#academy")
        if logo.content_type not in ("image/png", "image/jpeg", "image/svg+xml", "image/webp"):
            flash(request, "로고는 PNG, JPG, SVG, WEBP 파일만 올릴 수 있습니다.", "error")
            return _back("#academy")
        academy.logo = f"data:{logo.content_type};base64,{base64.b64encode(data).decode()}"
    db.commit()
    flash(request, "학원 정보를 저장했습니다.")
    return _back("#academy")


@router.post("/academy/invite")
def reset_invite(request: Request, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    academy = db.get(Academy, teacher.academy_id)
    code = make_code(8)
    while db.execute(select(Academy).where(Academy.invite_code == code)).scalar_one_or_none():
        code = make_code(8)
    academy.invite_code = code
    db.commit()
    flash(request, "선생님 초대 코드를 새로 만들었습니다. 예전 코드로는 더 이상 가입할 수 없어요.")
    return _back("#teachers")


@router.post("/pin")
def change_pin(
    request: Request,
    old_pin: str = Form(...),
    new_pin: str = Form(...),
    new_pin2: str = Form(...),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    if not verify_pin(old_pin, teacher.pin_hash):
        flash(request, "지금 PIN이 맞지 않습니다.", "error")
    elif not (new_pin.isdigit() and 4 <= len(new_pin) <= 8):
        flash(request, "새 PIN은 숫자 4~8자리로 정해 주세요.", "error")
    elif new_pin != new_pin2:
        flash(request, "새 PIN 확인이 맞지 않습니다.", "error")
    else:
        teacher.pin_hash = hash_pin(new_pin)
        db.commit()
        flash(request, "PIN을 바꿨습니다.")
    return _back("#account")
