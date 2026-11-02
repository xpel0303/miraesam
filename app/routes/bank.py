"""문법뱅크: 대분류·세부·난이도로 골라 담고 시험지(PDF/워드)로 만들기."""
import json
import random
import tempfile
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from urllib.parse import quote, urlencode

from ..bank import service
from ..db import get_db
from ..docx_builders import bank_docx
from ..models import Academy, BankQuestion, Teacher
from ..pdf import render_pdf
from ..web import current_teacher, flash, owner_only, render

router = APIRouter(prefix="/bank")
PER_PAGE = 30
MAX_CART = 80  # 세션 쿠키에 id 목록을 담으므로 상한을 둠
FILTERS = ("cat", "subcat", "level", "status", "q")


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _filters(src) -> dict:
    return {k: str(src.get(k) or "").strip()[:80] for k in FILTERS}


def _qs(f: dict, **extra) -> str:
    d = {**{k: v for k, v in f.items() if v}, **extra}
    return urlencode(d)


def _cart(request: Request) -> list[str]:
    return list(request.session.get("bank_cart") or [])


def _load(db: Session, ids: list[str]) -> list[BankQuestion]:
    by = {q.id: q for q in db.execute(select(BankQuestion).where(BankQuestion.id.in_(ids), BankQuestion.review != "del")).scalars()} if ids else {}
    return [by[i] for i in ids if i in by]


@router.get("")
def index(request: Request, page: int = 1, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = _filters(request.query_params)
    total_all = db.scalar(select(func.count()).select_from(BankQuestion)) or 0
    if not total_all:
        return render(request, "bank/index.html", {"teacher": teacher, "empty": True, "active": "grammar_bank", "cart": [], "f": f, "fac": {"cats": {}, "subs": {}, "books": [], "srcs": [], "qtypes": []}})
    stmt = service.apply_filters(select(BankQuestion), f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, ceil(total / PER_PAGE))
    page = max(1, min(page, pages))
    rows = db.execute(stmt.order_by(BankQuestion.ord).offset((page - 1) * PER_PAGE).limit(PER_PAGE)).scalars().all()
    cart = _cart(request)
    return render(request, "bank/index.html", {
        "teacher": teacher, "rows": [service.display(r) for r in rows], "f": f, "fac": service.facets(db), "total": total, "total_all": total_all,
        "page": page, "pages": pages, "cart": cart, "qs": _qs(f), "max_cart": MAX_CART, "active": "grammar_bank"})


def _add(request: Request, ids: list[str]) -> int:
    cart = _cart(request)
    added = 0
    for i in ids:
        if i not in cart and len(cart) < MAX_CART:
            cart.append(i)
            added += 1
    request.session["bank_cart"] = cart
    return added


@router.post("/add")
async def add(request: Request, teacher: Teacher = Depends(current_teacher)):
    f = await request.form()
    back = str(f.get("back") or "/bank")
    if not back.startswith("/bank"):
        back = "/bank"
    n = _add(request, [str(x) for x in f.getlist("id")])
    flash(request, f"{n}문항을 담았어요. (담은 문항 {len(_cart(request))}/{MAX_CART})" if n else f"담을 수 있는 문항이 없어요. (최대 {MAX_CART}개)", "ok" if n else "error")
    return _go(back)


@router.post("/pick")
async def pick(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """지금 필터에서 N문항을 무작위로 담기."""
    f = await request.form()
    flt = _filters(f)
    try:
        n = max(1, min(MAX_CART, int(f.get("n") or 10)))
    except ValueError:
        n = 10
    ids = [r for r in db.scalars(service.apply_filters(select(BankQuestion.id), flt))]
    random.shuffle(ids)
    got = _add(request, ids[:n])
    flash(request, f"무작위로 {got}문항을 담았어요. (담은 문항 {len(_cart(request))}/{MAX_CART})")
    return _go("/bank?" + _qs(flt))


@router.get("/cart")
def cart_view(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    qs = [service.display(q) for q in _load(db, _cart(request))]
    return render(request, "bank/cart.html", {"teacher": teacher, "qs": qs, "active": "grammar_bank", "max_cart": MAX_CART})


@router.post("/cart")
async def cart_edit(request: Request, teacher: Teacher = Depends(current_teacher)):
    f = await request.form()
    cart = _cart(request)
    act = str(f.get("act") or "")
    if act == "clear":
        cart = []
    elif act == "remove":
        gone = set(str(x) for x in f.getlist("id"))
        cart = [i for i in cart if i not in gone]
    elif act == "shuffle":
        random.shuffle(cart)
    request.session["bank_cart"] = cart
    return _go("/bank/cart")


def _sheet_ctx(request: Request, db: Session, teacher: Teacher, params) -> dict:
    qs = [service.display(q) for q in _load(db, _cart(request))]
    if not qs:
        raise HTTPException(400, "담은 문항이 없어요.")
    title = str(params.get("title") or "").strip()[:100] or "문법 시험지"
    return {"academy": db.get(Academy, teacher.academy_id), "doc_title": title, "title": title, "qs": qs,
            "answers": str(params.get("answers", "1")) != "0", "expl": str(params.get("expl", "0")) == "1"}


def _download(data: bytes, name: str, mime: str):
    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/sheet.pdf")
def sheet_pdf(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    ctx = _sheet_ctx(request, db, teacher, request.query_params)
    return _download(render_pdf("pdf/bank.html", ctx), f"{ctx['title']}.pdf", "application/pdf")


@router.get("/sheet.docx")
def sheet_docx(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    ctx = _sheet_ctx(request, db, teacher, request.query_params)
    return bank_docx(ctx["academy"], ctx["title"], ctx["qs"], ctx["answers"], ctx["expl"]).response(f"{ctx['title']}.docx")


@router.get("/import")
def import_page(request: Request, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    n = db.scalar(select(func.count()).select_from(BankQuestion)) or 0
    return render(request, "bank/import.html", {"teacher": teacher, "n": n, "active": "grammar_bank"})


@router.post("/import")
async def import_run(request: Request, file: UploadFile, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp.flush()
        try:
            n = service.import_file(db, tmp.name)
        except (ValueError, OSError):
            flash(request, "JSON 파일을 읽지 못했어요. 문법뱅크 통합 JSON을 올려 주세요.", "error")
            return _go("/bank/import")
    request.session["bank_cart"] = []
    flash(request, f"문법뱅크 {n:,}문항을 가져왔어요.")
    return _go("/bank")


REVIEW_PER_PAGE = 10


@router.get("/review")
def review(request: Request, page: int = 1, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """검수 화면: 문항을 큼직하게 보고 정상 / 고치기 / 삭제."""
    f = _filters(request.query_params)
    if not f["status"]:
        f["status"] = "todo"
    stmt = service.apply_filters(select(BankQuestion), f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, ceil(total / REVIEW_PER_PAGE))
    page = max(1, min(page, pages))
    rows = db.execute(stmt.order_by(BankQuestion.ord).offset((page - 1) * REVIEW_PER_PAGE).limit(REVIEW_PER_PAGE)).scalars().all()
    stats = dict(db.execute(select(BankQuestion.review, func.count()).group_by(BankQuestion.review)).all())
    return render(request, "bank/review.html", {
        "teacher": teacher, "rows": [service.display(r) for r in rows], "f": f, "fac": service.facets(db), "total": total,
        "page": page, "pages": pages, "stats": stats, "qs": _qs(f), "active": "grammar_bank"})


@router.post("/review/{qid}")
async def review_save(qid: str, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    q = db.get(BankQuestion, qid)
    if q is None:
        raise HTTPException(404, "문항을 찾을 수 없습니다.")
    f = await request.form()
    act = str(f.get("act") or "")
    if act in ("ok", "del", "todo"):
        q.review = "" if act == "todo" else act
    if act in ("save",) or f.get("edited") == "1":
        for k in ("instr", "stem", "choices", "answer", "passage"):
            if k in f:
                setattr(q, k, str(f.get(k)).replace("\r\n", "\n").strip())
        q.review = "fixed"
    db.commit()
    back = str(f.get("back") or "/bank/review")
    if not back.startswith("/bank"):
        back = "/bank/review"
    return _go(back)
