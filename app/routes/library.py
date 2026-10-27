"""지문 보관함: 저장한 지문 보기·고치기·추가, 그리고 자료 만들기 화면에서 불러오기(API)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..library.store import MIN_LEN, _auto_title, text_hash
from ..models import Passage, Teacher
from ..web import current_teacher, flash, render

router = APIRouter()


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, pid: int, teacher: Teacher) -> Passage:
    p = db.get(Passage, pid)
    if p is None or p.academy_id != teacher.academy_id:
        raise HTTPException(404, "지문을 찾을 수 없어요.")
    return p


def _query(db: Session, teacher: Teacher, q: str = "", group: str = ""):
    stmt = select(Passage).where(Passage.academy_id == teacher.academy_id)
    q = q.strip()[:60]
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Passage.title.ilike(like), Passage.text.ilike(like), Passage.source.ilike(like), Passage.group.ilike(like)))
    if group == "-":
        stmt = stmt.where(Passage.group == "")
    elif group:
        stmt = stmt.where(Passage.group == group)
    return stmt.order_by(Passage.updated_at.desc(), Passage.id.desc())


def _groups(db: Session, teacher: Teacher) -> list[tuple[str, int]]:
    return list(db.execute(select(Passage.group, func.count()).where(Passage.academy_id == teacher.academy_id)
                           .group_by(Passage.group).order_by(Passage.group)).all())


@router.get("/library")
def index(request: Request, q: str = "", group: str = "", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    items = list(db.execute(_query(db, teacher, q, group).limit(300)).scalars())
    return render(request, "library/index.html", {"teacher": teacher, "items": items, "groups": _groups(db, teacher), "q": q, "group": group,
                                                  "active": "library"})


@router.post("/library")
async def add(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """새 지문 넣기. 본문 칸에 여러 지문을 '===' 줄로 나눠 한 번에 넣을 수도 있음 (각 지문 첫 줄이 # 으로 시작하면 제목)."""
    f = await request.form()
    group, source = str(f.get("group", "")).strip()[:80], str(f.get("source", "")).strip()[:120]
    title = str(f.get("title", "")).strip()[:160]
    chunks = [c.strip() for c in str(f.get("text", "")).replace("\r\n", "\n").split("\n===") if c.strip()]
    added, dup = 0, 0
    for i, c in enumerate(chunks):
        c = c.lstrip("=").strip()
        t = title if len(chunks) == 1 else ""
        if c.startswith("#"):
            head, _, c = c.partition("\n")
            t, c = head.lstrip("#").strip()[:160], c.strip()
        if len(c) < MIN_LEN:
            continue
        h = text_hash(c)
        if db.execute(select(Passage.id).where(Passage.academy_id == teacher.academy_id, Passage.text_hash == h)).first():
            dup += 1
            continue
        db.add(Passage(academy_id=teacher.academy_id, teacher_id=teacher.id, title=t or _auto_title(c), text=c, source=source, group=group,
                       text_hash=h, used=0))
        added += 1
    db.commit()
    if added:
        flash(request, f"지문 {added}개를 저장했어요." + (f" (이미 있는 지문 {dup}개는 건너뛰었어요)" if dup else ""))
    else:
        flash(request, "저장할 지문이 없어요. " + ("이미 보관함에 있는 지문이에요." if dup else f"본문은 {MIN_LEN}자 이상이어야 해요."), "error")
    return _go("/library" + (f"?group={group}" if group else ""))


@router.post("/library/bulk")
async def bulk(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """여러 지문을 골라 분류 바꾸기 / 지우기."""
    f = await request.form()
    ids = [int(x) for x in f.getlist("id") if str(x).isdigit()]
    rows = list(db.execute(select(Passage).where(Passage.academy_id == teacher.academy_id, Passage.id.in_(ids))).scalars()) if ids else []
    if not rows:
        flash(request, "고른 지문이 없어요.", "error")
        return _go("/library")
    if f.get("action") == "delete":
        for p in rows:
            db.delete(p)
        msg = f"지문 {len(rows)}개를 지웠어요."
    else:
        g = str(f.get("group", "")).strip()[:80]
        for p in rows:
            p.group = g
        msg = f"지문 {len(rows)}개를 '{g or '분류 없음'}'(으)로 옮겼어요."
    db.commit()
    flash(request, msg)
    return _go("/library")


@router.get("/library/{pid}")
def edit(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    return render(request, "library/edit.html", {"teacher": teacher, "p": p, "groups": [g for g, _ in _groups(db, teacher) if g], "active": "library"})


@router.post("/library/{pid}")
async def save(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    f = await request.form()
    text = str(f.get("text", "")).strip()
    if len(text) < MIN_LEN:
        flash(request, f"본문은 {MIN_LEN}자 이상이어야 해요.", "error")
        return _go(f"/library/{pid}")
    p.title = str(f.get("title", "")).strip()[:160] or _auto_title(text)
    p.source, p.group = str(f.get("source", "")).strip()[:120], str(f.get("group", "")).strip()[:80]
    p.text, p.text_hash = text, text_hash(text)
    db.commit()
    flash(request, "저장했어요.")
    return _go("/library" + (f"?group={p.group}" if p.group else ""))


@router.post("/library/{pid}/delete")
def remove(pid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    p = _own(db, pid, teacher)
    db.delete(p)
    db.commit()
    flash(request, f"'{p.title}' 지문을 보관함에서 지웠어요. (이미 만든 자료는 그대로예요)")
    return _go("/library")


# ---------------- 자료 만들기 화면에서 불러오기 ----------------

def natural_key(p: Passage):
    """불러오기 순서: 분류별로, 제목을 '사람이 읽는 순서'로 (2번 < 10번 < 21번, 교과서 1과 < 교과서 4과). 제목이 같으면 넣은 순서."""
    import re

    t = (p.title or p.source or "").strip().lower()
    parts = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.split(r"(\d+)", t) if x)
    return (p.group or "", parts, p.id)


@router.get("/api/passages")
def api_list(q: str = "", group: str = "", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    items = sorted(db.execute(_query(db, teacher, q, group).limit(500)).scalars(), key=natural_key)
    return {"groups": [g for g, _ in _groups(db, teacher) if g],
            "items": [{"id": p.id, "title": p.title, "text": p.text, "source": p.source, "group": p.group,
                       "words": len(p.text.split())} for p in items]}
