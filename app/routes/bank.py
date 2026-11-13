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
from ..homework import spec as hspec
from ..models import Academy, BankQuestion, ClassGroup, Homework, HomeworkClass, Teacher
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
    qs = service.group_same_passage([service.display(q) for q in _load(db, _cart(request))])
    classes = list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())
    auto = sum(1 for q in qs if service.choice_number(q["answer"]) and q["objective"])
    auto_text = sum(1 for q in qs if not q["objective"] and q["src"] != "확인 필요" and not q.get("merged") and hspec.text_alts(q["answer"]))
    return render(request, "bank/cart.html", {"teacher": teacher, "qs": qs, "classes": classes, "auto": auto, "auto_text": auto_text, "active": "grammar_bank", "max_cart": MAX_CART})


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
    qs = service.group_same_passage([service.display(q) for q in _load(db, _cart(request))])
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


@router.get("/export.json")
def export(teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    """전체 문항 내보내기 (가져오기와 같은 형식 + 검수 상태). 고친 내용·삭제 표시도 그대로."""
    rows = db.execute(select(BankQuestion).order_by(BankQuestion.ord)).scalars().all()
    data = [{**{k: getattr(r, k) for k in service.FIELDS}, "no": r.no, "page": r.page, "review": r.review} for r in rows]
    body = json.dumps(data, ensure_ascii=False, indent=0)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%EB%AC%B8%EB%B2%95%EB%B1%85%ED%81%AC_%EB%82%B4%EB%B3%B4%EB%82%B4%EA%B8%B0.json"})


@router.get("/export_units.json")
def export_units(teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    """교재 단원 묶음 목록 (분류 재배치용, 문항 원문 대신 예시 1~2개만)."""
    body = json.dumps(service.unit_groups(db), ensure_ascii=False)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%EB%AC%B8%EB%B2%95%EB%B1%85%ED%81%AC_%EB%8B%A8%EC%9B%90%EB%AA%A9%EB%A1%9D.json"})


@router.post("/remap")
async def remap_run(request: Request, file: UploadFile, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    """분류 재배치 파일(JSON)을 올리면 작업으로 전체 문항 분류를 바꿈."""
    from ..jobs import enqueue

    try:
        mapping = json.loads((await file.read()).decode("utf-8"))
        assert isinstance(mapping, list)
    except Exception:  # noqa: BLE001
        flash(request, "분류 재배치 파일(JSON)을 읽지 못했어요.", "error")
        return _go("/bank/import")
    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="bank_remap", title=f"문법뱅크 분류 재배치 ({len(mapping):,}묶음)",
                  payload={"mapping": mapping})
    return _go(f"/bank/import?job={job.id}")


@router.post("/remap_rules")
def remap_rules_run(request: Request, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    """규칙(교재 학년 단계 + 기존 분류)으로 전체 문항 재배치."""
    from ..jobs import enqueue

    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="bank_remap", title="문법뱅크 분류 재배치 (규칙)", payload={"rules": True})
    return _go(f"/bank/import?job={job.id}")


@router.get("/import")
def import_page(request: Request, job: int | None = None, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    n = db.scalar(select(func.count()).select_from(BankQuestion)) or 0
    mapped = db.scalar(select(func.count()).select_from(BankQuestion).where(BankQuestion.sub_id != "")) or 0
    return render(request, "bank/import.html", {"teacher": teacher, "n": n, "mapped": mapped, "job": job, "active": "grammar_bank"})


@router.post("/import")
async def import_run(request: Request, file: UploadFile, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp.flush()
        try:
            form = await request.form()
            n = service.import_file(db, tmp.name, keep_reviewed=form.get("overwrite") != "1")
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


def _student_q(q: dict) -> dict:
    """학생 화면에 보일 문제 (정답·해설은 넣지 않음). 숙제를 낼 때 복사해 두므로 나중에 문항을 고쳐도 숙제는 그대로."""
    return {k: q[k] for k in ("head", "passage", "stem", "choices")}


@router.post("/homework")
async def make_homework(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """담은 문항을 숙제로: 보기 고르는 문항은 번호로 자동 채점, 서술형은 '종이 답안 확인' 할 일로."""
    f = await request.form()
    qs = service.group_same_passage([service.display(q) for q in _load(db, _cart(request))])
    title = str(f.get("title") or "").strip()[:120]
    class_ids = {int(x) for x in f.getlist("class_id") if str(x).isdigit()}
    valid = set(db.execute(select(ClassGroup.id).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.id.in_(class_ids))).scalars()) if class_ids else set()
    if not qs or not title or not valid:
        flash(request, "담은 문항, 숙제 이름, 반을 모두 확인해 주세요.", "error")
        return _go("/bank/cart")
    items, written = [], []
    for n, q in enumerate(qs, 1):
        k = service.choice_number(q["answer"]) if q["objective"] else None
        alts = [] if (q["objective"] or q["src"] == "확인 필요" or q.get("merged")) else hspec.text_alts(q["answer"])
        if k:
            items.append({"n": n, "a": k})
            if f.get("show_q") == "1":
                items[-1]["q"] = _student_q(q)
        elif alts and f.get("auto_text") == "1":
            items.append({"n": n, "a": alts[0], "alts": alts, "txt": True})
            if f.get("show_q") == "1":
                items[-1]["q"] = _student_q(q)
        else:
            written.append(n)
    tasks = []
    if items:
        tasks.append(hspec.task_omr(f"{title} · 답 입력", "bank", items, f.get("pass") or 0))
    if written and f.get("written") == "1":
        nums = ", ".join(map(str, written))
        tasks.append(hspec.task_check(f"서술형 {nums}번: 종이에 풀어서 선생님께 확인받기"))
    if not tasks:
        flash(request, "숙제로 낼 수 있는 문항이 없어요. (보기를 고르는 문항이 없고 서술형 확인도 꺼져 있어요)", "error")
        return _go("/bank/cart")
    due = str(f.get("due") or "").strip()
    h = Homework(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, due=due if len(due) == 10 else "",
                 memo=f"문법뱅크 시험지 {len(qs)}문항 (번호는 인쇄한 시험지와 같아요)", tasks=tasks, options={"reveal": f.get("reveal") == "1"})
    db.add(h)
    db.flush()
    for cid in valid:
        db.add(HomeworkClass(homework_id=h.id, class_id=cid))
    db.commit()
    flash(request, f"숙제를 냈어요. 자동 채점 {len(items)}문항(영어로 쓰는 {sum(1 for x in items if x.get('txt'))}문항 포함)" + (f", 서술형 {len(written)}문항은 종이 확인." if written and f.get("written") == "1" else "."))
    return _go(f"/homework/{h.id}")


@router.get("/build")
def build_page(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """문제 만들기: 난이도 → 문법 항목 → 세부 항목 → 문항 수 순서로 골라 한 번에 뽑기."""
    fac = service.facets(db)
    return render(request, "bank/build.html", {"teacher": teacher, "cats": fac["cats"], "subs_json": json.dumps(fac["subs"], ensure_ascii=False),
                                               "max_cart": MAX_CART, "active": "grammar_bank"})


@router.post("/build")
async def build_run(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    items = []
    for raw in f.getlist("item"):
        cat, _, sub = str(raw).partition("||")
        if cat:
            items.append((cat.strip(), sub.strip()))
    if not items:
        flash(request, "문법 항목을 하나 이상 골라 주세요.", "error")
        return _go("/bank/build")
    levels = [x for x in f.getlist("level") if x in ("하", "중", "상")]
    num = lambda k: max(0, min(MAX_CART, int(f.get(k) or 0))) if str(f.get(k) or "0").isdigit() else 0
    per_item = {i: num(f"c{i}") for i in range(len(items))} if f.get("mode") == "manual" else None
    n_obj, n_sub = num("n_obj"), num("n_sub")
    total = sum(per_item.values()) if per_item else n_obj + n_sub
    if total == 0:
        flash(request, "문항 수를 정해 주세요.", "error")
        return _go("/bank/build")
    ids = service.pick_questions(db, items, levels, n_obj, n_sub, per_item, skip_unsure=f.get("unsure") != "1", with_passage=f.get("passage") != "0")
    ids = ids[:MAX_CART]
    if f.get("shuffle") == "1":
        random.shuffle(ids)
    else:  # 교재 순서(대분류·세부 순)로 정렬 — 같은 지문 문항이 붙어 있게
        order = dict(db.execute(select(BankQuestion.id, BankQuestion.ord).where(BankQuestion.id.in_(ids))).all())
        ids.sort(key=lambda i: order.get(i, 0))
    request.session["bank_cart"] = ids
    if len(ids) < total:
        flash(request, f"조건에 맞는 문항이 모자라 {len(ids)}문항만 뽑았어요.", "error")
    else:
        flash(request, f"{len(ids)}문항을 뽑았어요. 아래에서 시험지로 받거나 숙제로 내세요.")
    return _go("/bank/cart")
