"""템플릿·로그인 공통 도구."""
from datetime import timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import APP_NAME, APP_SUB, BASE_DIR
from .db import get_db
from .features import STAGE_NAMES, grouped
from .models import Teacher

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(APP_NAME=APP_NAME, APP_SUB=APP_SUB, NAV_GROUPS=grouped, STAGE_NAMES=STAGE_NAMES)

_KST = ZoneInfo("Asia/Seoul")


def _kst(dt, fmt="%m/%d %H:%M"):
    """DB 시각(UTC)을 한국 시간으로."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime(fmt)


templates.env.filters["kst"] = _kst


class LoginRequired(Exception):
    """로그인 안 된 화면 요청 → 로그인 화면으로 보냄 (main.py 에서 처리)."""


def current_teacher(request: Request, db: Session = Depends(get_db)) -> Teacher:
    tid = request.session.get("teacher_id")
    teacher = db.get(Teacher, tid) if tid else None
    if teacher is None:
        request.session.clear()
        if request.url.path.startswith("/api/"):
            raise HTTPException(401, "로그인이 필요합니다.")
        raise LoginRequired()
    return teacher


def owner_only(teacher: Teacher = Depends(current_teacher)) -> Teacher:
    if teacher.role != "owner":
        raise HTTPException(403, "원장 계정만 할 수 있는 작업입니다.")
    return teacher


def flash(request: Request, message: str, kind: str = "ok"):
    request.session.setdefault("_flash", []).append({"kind": kind, "text": message})


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    ctx = dict(ctx or {})
    ctx.setdefault("flashes", request.session.pop("_flash", []))
    ctx.setdefault("active", "")
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
