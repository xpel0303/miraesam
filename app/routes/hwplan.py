"""단어 숙제 자동 일정 화면 (/homework/plans)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..care import KST
from ..db import get_db
from ..homework import plan as P
from ..homework import spec as hspec
from ..models import ClassGroup, HomeworkPlan, Teacher, WordList
from ..vocab.spec import QUIZ_MODES
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/homework/plans")
HOURS = list(range(5, 24))


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, pid, teacher) -> HomeworkPlan:
    p = db.get(HomeworkPlan, pid)
    if p is None or p.academy_id != teacher.academy_id:
        raise HTTPException(404, "자동 숙제 일정을 찾을 수 없습니다.")
    return p


def _lists(db, teacher):
    ls = list(db.execute(select(WordList).where(WordList.academy_id == teacher.academy_id, WordList.status == "done")
                         .order_by(WordList.is_base.desc(), WordList.id.desc()).limit(300)).scalars())
    return [(wl, P.units_of(wl)) for wl in ls if len(P.units_of(wl)) >= 2]


def _ctx(db, teacher, **kw):
    classes = list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.active.is_(True))
                              .order_by(ClassGroup.name)).scalars())
    return {"teacher": teacher, "classes": classes, "lists": _lists(db, teacher), "weekdays": P.WEEKDAYS, "hours": HOURS,
            "stage_sets": hspec.STAGE_SETS, "modes": QUIZ_MODES, "active": "care", **kw}


def _read(f, db, teacher) -> tuple[dict, list[str]]:
    errs = []
    g = lambda k, d="": str(f.get(k, d) or d).strip()
    form = {"title": g("title")[:80], "list_id": int(g("list_id", "0")) if g("list_id", "0").isdigit() else 0,
            "class_ids": [int(x) for x in f.getlist("class_id") if str(x).isdigit()],
            "weekdays": sorted({int(x) for x in f.getlist("weekday") if str(x).isdigit() and int(x) < 7}),
            "start_date": g("start_date"), "hour": int(g("hour", "7")) if g("hour", "7").isdigit() else 7,
            "start_unit": int(g("start_unit", "0")) if g("start_unit", "0").isdigit() else 0,
            "gap": int(g("gap", "2")) if g("gap", "2").isdigit() else 2,
            "settings": {"stages": g("stages", "all") if g("stages", "all") in hspec.STAGE_SETS else "all",
                         "mode": g("mode", "mc_en") if g("mode", "mc_en") in QUIZ_MODES else "mc_en",
                         "pass": max(0, min(100, int(g("pass", "80")))) if g("pass", "80").isdigit() else 80,
                         "due": "same" if g("due") == "same" else "next"}}
    wl = db.get(WordList, form["list_id"]) if form["list_id"] else None
    if wl is None or wl.academy_id != teacher.academy_id:
        errs.append("단어장을 골라 주세요.")
    if not form["class_ids"]:
        errs.append("숙제를 받을 반을 골라 주세요.")
    if not form["weekdays"]:
        errs.append("숙제를 낼 요일을 하나 이상 골라 주세요.")
    try:
        datetime.strptime(form["start_date"], "%Y-%m-%d")
    except ValueError:
        errs.append("시작 날짜를 골라 주세요.")
    if not form["title"]:
        form["title"] = (wl.title if wl else "단어 숙제")[:80]
    return form, errs


@router.get("/new")
def new(request: Request, list_id: int = 0, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    form = {"list_id": list_id, "class_ids": [], "weekdays": [0, 2, 4], "start_date": datetime.now(KST).date().isoformat(), "hour": 7,
            "start_unit": 0, "gap": 2, "settings": {"stages": "all", "mode": "mc_en", "pass": 80, "due": "next"}}
    return render(request, "hwplan/new.html", _ctx(db, teacher, form=form))


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    form, errs = _read(f, db, teacher)
    if errs:
        return render(request, "hwplan/new.html", _ctx(db, teacher, form=form, errors=errs), 400)
    p = HomeworkPlan(academy_id=teacher.academy_id, teacher_id=teacher.id, **form)
    db.add(p)
    db.commit()
    made = P.catch_up(academy_id=teacher.academy_id)
    flash(request, "자동 숙제 일정을 만들었어요." + (" 오늘 회차 숙제도 바로 냈어요." if made else " 정한 요일·시각이 되면 숙제가 저절로 나가요."))
    return _go(f"/homework/plans/{p.id}")


@router.get("/{pid}")
def detail(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    P.catch_up(academy_id=teacher.academy_id)
    p = _own(db, pid, teacher)
    db.refresh(p)
    wl = db.get(WordList, p.list_id)
    form = {k: getattr(p, k) for k in ("title", "list_id", "class_ids", "weekdays", "start_date", "hour", "start_unit", "gap", "settings")}
    return render(request, "hwplan/detail.html", _ctx(db, teacher, p=p, wl=wl, table=P.plan_table(p, wl), form=form,
                                                      today=datetime.now(KST).date().isoformat()))


@router.post("/{pid}")
async def update(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """이미 낸 회차는 그대로, 앞으로 낼 회차에만 적용."""
    p = _own(db, pid, teacher)
    f = await request.form()
    form, errs = _read(f, db, teacher)
    if errs:
        flash(request, " ".join(errs), "error")
        return _go(f"/homework/plans/{pid}")
    if p.made:  # 이미 낸 회차가 있으면 단어장·시작 Day·복습 간격은 못 바꿈 (회차가 꼬이지 않게)
        for k in ("list_id", "start_unit", "gap", "start_date"):
            form.pop(k)
    for k, v in form.items():
        setattr(p, k, v)
    db.commit()
    P.catch_up(academy_id=teacher.academy_id)
    flash(request, "바꿨어요. 앞으로 낼 숙제부터 적용돼요.")
    return _go(f"/homework/plans/{pid}")


@router.post("/{pid}/toggle")
def toggle(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    p.active = not p.active
    db.commit()
    flash(request, "다시 시작했어요." if p.active else "멈췄어요. 다시 시작하면 멈춘 다음 회차부터 다시 시작한 날 이후 요일에 이어서 나가요.")
    if p.active:  # 멈춘 동안의 날짜는 건너뛰고 오늘부터 다시 셈
        p.settings = {**(p.settings or {}), "resume": datetime.now(KST).date().isoformat()}
        db.commit()
    return _go(f"/homework/plans/{pid}")


@router.post("/{pid}/delete")
def remove(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    db.delete(p)
    db.commit()
    flash(request, "자동 숙제 일정을 지웠어요. 이미 낸 숙제는 그대로 있어요.")
    return _go("/homework")
