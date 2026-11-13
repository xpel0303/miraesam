"""문법뱅크: 대분류·세부·난이도로 골라 담고 시험지(PDF/워드)로 만들기."""
import json
import random
import tempfile
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from urllib.parse import quote, urlencode

from ..bank import service
from ..db import get_db
from ..docx_builders import bank_docx
from ..homework import spec as hspec
from ..models import Academy, BankCart, BankConcept, BankQuestion, ClassGroup, Homework, HomeworkClass, Job, Teacher
from ..pdf import render_pdf
from ..web import current_teacher, flash, owner_only, render

router = APIRouter(prefix="/bank")
PER_PAGE = 30
MAX_CART = 600  # 한 번에 담는 최대 문항 수 (세트 전체 합)
FILTERS = ("cat", "subcat", "level", "status", "q")


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _filters(src) -> dict:
    return {k: str(src.get(k) or "").strip()[:80] for k in FILTERS}


def _qs(f: dict, **extra) -> str:
    d = {**{k: v for k, v in f.items() if v}, **extra}
    return urlencode(d)


def _cart_row(db: Session, teacher: Teacher) -> BankCart | None:
    return db.get(BankCart, teacher.id)


def _cart(db: Session, teacher: Teacher) -> list[str]:
    row = _cart_row(db, teacher)
    return list(row.ids or []) if row else []


def _cart_meta(db: Session, teacher: Teacher) -> dict:
    row = _cart_row(db, teacher)
    return dict(row.meta or {}) if row else {}


def _set_cart(db: Session, teacher: Teacher, ids: list[str], meta: dict | None = None):
    """담은 문항 저장. meta 를 안 주면 세트·개념 설정은 지움 (직접 담기·빼기 등)."""
    row = _cart_row(db, teacher)
    if row is None:
        row = BankCart(teacher_id=teacher.id)
        db.add(row)
    row.ids = list(ids)
    row.meta = meta or {}
    db.commit()


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
    cart = _cart(db, teacher)
    return render(request, "bank/index.html", {
        "teacher": teacher, "rows": [service.display(r) for r in rows], "f": f, "fac": service.facets(db), "total": total, "total_all": total_all,
        "page": page, "pages": pages, "cart": cart, "qs": _qs(f), "max_cart": MAX_CART, "active": "grammar_bank"})


def _add(db: Session, teacher: Teacher, ids: list[str]) -> int:
    cart = _cart(db, teacher)
    added = 0
    for i in ids:
        if i not in cart and len(cart) < MAX_CART:
            cart.append(i)
            added += 1
    _set_cart(db, teacher, cart)
    return added


@router.post("/add")
async def add(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    back = str(f.get("back") or "/bank")
    if not back.startswith("/bank"):
        back = "/bank"
    n = _add(db, teacher, [str(x) for x in f.getlist("id")])
    flash(request, f"{n}문항을 담았어요. (담은 문항 {len(_cart(db, teacher))}/{MAX_CART})" if n else f"담을 수 있는 문항이 없어요. (최대 {MAX_CART}개)", "ok" if n else "error")
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
    got = _add(db, teacher, ids[:n])
    flash(request, f"무작위로 {got}문항을 담았어요. (담은 문항 {len(_cart(db, teacher))}/{MAX_CART})")
    return _go("/bank?" + _qs(flt))


@router.get("/cart")
def cart_view(request: Request, job: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    qs = service.group_same_passage([service.display(q) for q in _load(db, _cart(db, teacher))])
    classes = list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())
    auto = sum(1 for q in qs if service.choice_number(q["answer"]) and q["objective"])
    auto_text = sum(1 for q in qs if not q["objective"] and q["src"] != "확인 필요" and not q.get("merged") and hspec.text_alts(q["answer"]))
    meta = _cart_meta(db, teacher)
    concept_subs = meta.get("concept") or []
    ready = db.scalar(select(func.count()).select_from(BankConcept).where(BankConcept.sub_id.in_(concept_subs))) if concept_subs else 0
    return render(request, "bank/cart.html", {"teacher": teacher, "qs": qs, "classes": classes, "auto": auto, "auto_text": auto_text, "active": "grammar_bank",
                                              "max_cart": MAX_CART, "job": job, "sets": [len(x) for x in meta.get("sets") or []],
                                              "concept_total": len(concept_subs), "concept_ready": ready or 0,
                                              "concepts": _concept_cards(db, concept_subs) if concept_subs else [], "pdf": meta.get("pdf") or {}})


@router.post("/cart")
async def cart_edit(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    cart = _cart(db, teacher)
    act = str(f.get("act") or "")
    if act == "clear":
        cart = []
    elif act == "remove":
        gone = set(str(x) for x in f.getlist("id"))
        cart = [i for i in cart if i not in gone]
    elif act == "shuffle":
        random.shuffle(cart)
    _set_cart(db, teacher, cart)
    return _go("/bank/cart")


def _concept_cards(db: Session, sub_ids: list[str]) -> list[dict]:
    from ..bank import taxonomy

    subs = taxonomy.load()["subs"]
    have = {c.sub_id: c.body for c in db.execute(select(BankConcept).where(BankConcept.sub_id.in_(sub_ids))).scalars()} if sub_ids else {}
    return [{**have[x], "path": f"{subs[x]['tier_label']} · {subs[x]['cat']} · {subs[x]['label']}"} for x in sub_ids if x in have and x in subs]


def _sheet_ctx(request: Request, db: Session, teacher: Teacher, params) -> dict:
    meta = _cart_meta(db, teacher)
    title = str(params.get("title") or "").strip()[:100] or "문법 시험지"
    if meta.get("sets"):
        parts = [{"title": f"{title} · {chr(65 + k)}세트", "qs": service.group_same_passage([service.display(q) for q in _load(db, ids)])}
                 for k, ids in enumerate(meta["sets"])]
        parts = [p for p in parts if p["qs"]]
    else:
        parts = [{"title": title, "qs": service.group_same_passage([service.display(q) for q in _load(db, _cart(db, teacher))])}]
    if not parts or not parts[0]["qs"]:
        raise HTTPException(400, "담은 문항이 없어요.")
    with_concept = str(params.get("concept", "1")) != "0"
    return {"academy": db.get(Academy, teacher.academy_id), "doc_title": title, "title": title, "qs": parts[0]["qs"], "parts": parts,
            "concepts": _concept_cards(db, meta.get("concept") or []) if with_concept else [],
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
    return bank_docx(ctx["academy"], ctx["title"], ctx["qs"], ctx["answers"], ctx["expl"], ctx["parts"], ctx["concepts"]).response(f"{ctx['title']}.docx")


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


@router.post("/fill")
def fill_run(request: Request, per_cat: int = Form(200), per_sub: int = Form(0), resume: int = Form(0), teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    """문항이 하나도 없는 중분류를 AI 문항으로 채우기 (중분류마다 per_cat 문항)."""
    from ..jobs import enqueue

    per_cat, per_sub = max(10, min(1000, per_cat)), max(0, min(200, per_sub))
    if resume:  # 같은 소분류를 두 작업이 동시에 만들지 않게: 그 작업이나 그걸 이어받은 작업이 아직 돌고 있으면 막음
        for j in db.execute(select(Job).where(Job.kind == "bank_fill", Job.status.in_(("queued", "running")))).scalars():
            if j.id == resume or (j.payload or {}).get("resume") == resume:
                flash(request, f"작업 {j.id}이(가) 아직 돌고 있어요. 끝난 뒤에 이어서 눌러 주세요.")
                return _go(f"/bank/import?job={j.id}")
    title = f"멈춘 채우기 작업({resume}) 이어서" if resume else f"빈 소분류 AI 문항 채우기 (소분류당 {per_sub})" if per_sub else f"빈 중분류 AI 문항 채우기 (중분류당 {per_cat})"
    job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="bank_fill", title=title,
                  payload={"per_cat": per_cat, "per_sub": per_sub, **({"resume": resume} if resume else {})})
    return _go(f"/bank/import?job={job.id}")


@router.get("/import")
def import_page(request: Request, job: int | None = None, teacher: Teacher = Depends(owner_only), db: Session = Depends(get_db)):
    n = db.scalar(select(func.count()).select_from(BankQuestion)) or 0
    mapped = db.scalar(select(func.count()).select_from(BankQuestion).where(BankQuestion.sub_id != "")) or 0
    return render(request, "bank/import.html", {"teacher": teacher, "n": n, "mapped": mapped, "job": job, "active": "grammar_bank",
                                                "fills": _fill_jobs(db, teacher), "empty_subs": _empty_subs(db)})


def _empty_subs(db: Session) -> int:
    """문항이 하나도 없는(삭제 표시 빼고) 소분류 수."""
    from ..bank import taxonomy

    have = set(db.scalars(select(BankQuestion.sub_id).where(BankQuestion.review != "del", BankQuestion.sub_id != "").distinct()))
    return sum(1 for sid in taxonomy.load()["subs"] if sid not in have)


def _fill_jobs(db: Session, teacher: Teacher) -> list[dict]:
    """최근 AI 문항 채우기 작업: 상태, 목표 대비 지금 들어 있는 AI 문항 수, 이어받을 수 있는지."""
    from ..handlers.bank import GEN_BOOK

    jobs = db.execute(select(Job).where(Job.kind == "bank_fill", Job.academy_id == teacher.academy_id).order_by(Job.id.desc()).limit(8)).scalars().all()
    have = dict(db.execute(select(BankQuestion.sub_id, func.count()).where(BankQuestion.book == GEN_BOOK, BankQuestion.review != "del")
                           .group_by(BankQuestion.sub_id)).all()) if jobs else {}
    active = {j.id for j in jobs if j.status in ("queued", "running")} | {(j.payload or {}).get("resume") for j in jobs if j.status in ("queued", "running")}
    resumed = {(j.payload or {}).get("resume") for j in jobs}
    out = []
    for j in jobs:
        targets = (j.payload or {}).get("targets") or {}
        need = sum(targets.values())
        got = sum(min(have.get(s, 0), n) for s, n in targets.items())
        short = sum(1 for s, n in targets.items() if have.get(s, 0) < n)
        out.append({"id": j.id, "title": j.title, "status": j.status, "step": j.step, "progress": j.progress, "attempts": j.attempts,
                    "error": j.error, "reply": (j.result or {}).get("reply"), "subs": len(targets), "need": need, "got": got, "short": short,
                    "created": j.created_at, "can_resume": bool(targets) and short > 0 and j.id not in active and j.status in ("done", "failed")
                    and j.id not in resumed})
    return out


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
    _set_cart(db, teacher, [])
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
    sets = _cart_meta(db, teacher).get("sets") or []
    qs = service.group_same_passage([service.display(q) for q in _load(db, sets[0] if sets else _cart(db, teacher))])
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
    """문제 만들기: 학년 단계 → 중분류 → 소분류를 체크하고, 소분류당 문항 수·형식·난이도를 정해 한 번에 뽑기."""
    return render(request, "bank/build.html", {"teacher": teacher, "tree_json": json.dumps(service.tree(db), ensure_ascii=False),
                                               "max_cart": MAX_CART, "active": "grammar_bank"})


@router.post("/build")
async def build_run(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    from ..bank import taxonomy

    f = await request.form()
    subs = taxonomy.load()["subs"]
    sub_ids = list(dict.fromkeys(x for x in f.getlist("sub") if x in subs))
    if not sub_ids:
        flash(request, "출제할 소분류를 하나 이상 골라 주세요.", "error")
        return _go("/bank/build")
    per = int(f.get("per")) if str(f.get("per") or "").isdigit() else 3
    per = max(1, min(30, per))
    kinds = [k for k in f.getlist("kind") if k in service.KINDS]
    if not kinds:
        flash(request, "문항 형식(객관식·주관식·서술형)을 하나 이상 켜 주세요.", "error")
        return _go("/bank/build")
    levels = [x for x in f.getlist("level") if x in ("하", "중", "상")]
    n_sets = int(f.get("sets")) if str(f.get("sets") or "").isdigit() else 1
    n_sets = max(1, min(10, n_sets))
    sets = service.pick_by_subs(db, sub_ids, per, kinds, levels, skip_unsure=f.get("unsure") != "1", with_passage=f.get("passage") != "0", sets=n_sets)
    want = len(sub_ids) * per
    allq = sum(len(x) for x in sets)
    cut = allq > MAX_CART
    if cut:  # 세트 전체 합이 상한을 넘으면 세트마다 고르게 자름
        each = MAX_CART // len(sets)
        sets = [x[:each] for x in sets]
    order = dict(db.execute(select(BankQuestion.id, BankQuestion.ord).where(BankQuestion.id.in_([i for x in sets for i in x]))).all())
    for x in sets:
        if f.get("shuffle") == "1":
            random.shuffle(x)
        else:  # 분류 체계 순서 — 같은 소분류·지문 문항이 붙어 있게
            x.sort(key=lambda i: order.get(i, 0))
    sets = [x for x in sets if x]
    if not sets:
        flash(request, "조건에 맞는 문항이 없어요. 형식·난이도를 넓혀 보세요.", "error")
        return _go("/bank/build")
    meta = {"sets": sets if len(sets) > 1 else [], "concept": sub_ids if f.get("concept") == "1" else [],
            "pdf": {"title": str(f.get("ptitle") or "").strip()[:100] or "문법 시험지", "answers": f.get("answers") != "0", "expl": f.get("expl") == "1"}}
    _set_cart(db, teacher, [i for x in sets for i in x], meta)
    job_q = ""
    if meta["concept"]:
        have = set(db.execute(select(BankConcept.sub_id).where(BankConcept.sub_id.in_(sub_ids))).scalars())
        todo = [x for x in sub_ids if x not in have]
        if todo:
            if not teacher.gemini_key_enc:
                flash(request, "개념 설명을 만들려면 설정에서 Gemini API 키를 넣어 주세요. (문항은 뽑아 두었어요)", "error")
            else:
                from ..jobs import enqueue

                job = enqueue(db, academy_id=teacher.academy_id, teacher_id=teacher.id, kind="bank_concepts",
                              title=f"문법 개념 설명 만들기 ({len(todo)}개)", payload={"subs": todo})
                job_q = f"?job={job.id}"
    sizes = " · ".join(f"{chr(65 + k)}세트 {len(x)}" for k, x in enumerate(sets)) if len(sets) > 1 else f"{len(sets[0])}문항"
    if cut:
        flash(request, f"한 번에 {MAX_CART}문항까지라 세트마다 줄였어요 ({sizes}).", "error")
    elif any(len(x) < want for x in sets):
        flash(request, f"켠 형식·난이도의 문항이 모자란 문법이 있어 일부만 뽑았어요 ({sizes}, 세트당 목표 {want}).", "error")
    else:
        flash(request, f"뽑았어요: {sizes}. 아래에서 시험지로 받거나 숙제로 내세요.")
    return _go("/bank/cart" + job_q)

