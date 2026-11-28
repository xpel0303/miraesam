"""템플릿·로그인 공통 도구."""
from datetime import timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from .config import APP_NAME, APP_SUB, BASE_DIR
from .db import get_db
from .features import STAGE_NAMES, grouped
from .models import Teacher

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(APP_NAME=APP_NAME, APP_SUB=APP_SUB, NAV_GROUPS=grouped, STAGE_NAMES=STAGE_NAMES)
from . import pwa_brand  # noqa: E402
templates.env.globals["pwa_brand"] = pwa_brand
templates.env.tests["allowed"] = lambda f, t=None: (t is None) or t.can(f.key)  # 선생님 권한으로 메뉴 거르기

_KST = ZoneInfo("Asia/Seoul")


def _kst(dt, fmt="%m/%d %H:%M"):
    """DB 시각(UTC)을 한국 시간으로."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime(fmt)


templates.env.filters["kst"] = _kst


def _u(text) -> Markup:
    """학생 화면용 글: 모두 이스케이프한 뒤 <u>…</u> 밑줄만 살리고, 줄바꿈은 <br>."""
    t = str(escape(text or "")).replace("&lt;u&gt;", "<u>").replace("&lt;/u&gt;", "</u>")
    return Markup(t.replace("\n", "<br>"))


templates.env.filters["u"] = _u

from .bank.service import rich as _rich  # noqa: E402

templates.env.filters["bt"] = _rich  # 문법뱅크 문항: 〔…〕 밑줄


templates.env.filters["fmt"] = _rich


class NoPermission(Exception):
    """원장이 이 선생님에게 열어 주지 않은 기능."""


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
    if teacher.role != "owner" and teacher.perms is not None:
        from .features import feature_of
        key = feature_of(request.url.path)
        if key and key not in teacher.perms:
            raise NoPermission(key)
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
    t = ctx.get("teacher")
    if t is not None and getattr(t, "academy", None) is not None and t.academy.name:  # 선생님 화면: 탭 제목 등은 학원 이름으로
        ctx.setdefault("APP_NAME", t.academy.name)
        ctx.setdefault("APP_SUB", "")
    ctx.setdefault("active", "")
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def merge_grammar(*parts) -> str:
    """'관계대명사 what / 분사구문' 처럼 / 로 이은 목표어법 여러 개를 겹치지 않게 합침 (지문별 + 전체)."""
    out = []
    for part in parts:
        for g in str(part or "").split("/"):
            g = " ".join(g.split())
            if g and g not in out:
                out.append(g)
    return " / ".join(out)[:300]
