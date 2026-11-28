"""단어장 (선생님 화면)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..library.store import remember_form
from ..docx_builders import vocab_list_docx, vocab_test_docx
from ..jobs import enqueue
from ..models import Academy, ClassGroup, Job, QuizResult, Student, Teacher, WordList, WordListClass
from ..vocab import spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/vocab")

LEVELS = ["", "중등", "고1", "고2", "고3", "수능"]


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, lid: int, teacher: Teacher) -> WordList:
    wl = db.get(WordList, lid)
    if wl is None or wl.academy_id != teacher.academy_id:
        raise HTTPException(404, "단어장을 찾을 수 없습니다.")
    return wl


def _busy(db: Session, wl: WordList) -> bool:
    job = db.get(Job, wl.last_job_id) if wl.last_job_id else None
    return bool(job and job.status in ("queued", "running") and job.kind == "vocab_build")


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    lists = list(db.execute(
        select(WordList).options(selectinload(WordList.teacher), selectinload(WordList.classes))
        .where(WordList.academy_id == teacher.academy_id, WordList.is_base.is_not(True)).order_by(WordList.id.desc()).limit(200)
    ).scalars())
    base = list(db.execute(select(WordList).options(selectinload(WordList.classes))
                           .where(WordList.academy_id == teacher.academy_id, WordList.is_base.is_(True)).order_by(WordList.title)).scalars())
    rank = {"초등": 0, "중등": 1, "고1": 2, "고2": 3, "고3": 4}
    base.sort(key=lambda w: (rank.get(w.level, 9), w.title))
    return render(request, "vocab/index.html", {"teacher": teacher, "lists": lists, "base": base, "sources": spec.sources, "active": "vocab"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "vocab/new.html", {"teacher": teacher, "form": {"kind": "passage", "count": 10, "passages": [{}]}, "levels": LEVELS, "max": spec.MAX_WORDS,
                                                 "maxp": spec.MAX_PASSAGES, "active": "vocab"})


@router.get("/base")
def base_form(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    base = list(db.execute(select(WordList).where(WordList.academy_id == teacher.academy_id, WordList.is_base.is_(True)).order_by(WordList.title)).scalars())
    return render(request, "vocab/base.html", {"teacher": teacher, "base": base, "active": "vocab"})


@router.post("/base")
async def base_upload(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """기본 단어장 올리기: 엑셀(.xlsx 여러 개 또는 .zip). 같은 제목의 기본 단어장이 있으면 단어를 새것으로 바꿈."""
    from ..vocab.base_import import parse_xlsx, read_upload
    f = await request.form()
    done, bad = [], []
    for up in f.getlist("files"):
        if not getattr(up, "filename", ""):
            continue
        data = await up.read()
        if len(data) > 40_000_000:
            bad.append(f"{up.filename} (너무 커요)")
            continue
        try:
            books = read_upload(up.filename, data)
        except Exception:
            bad.append(f"{up.filename} (열 수 없는 파일)")
            continue
        for name, x in books:
            try:
                r = parse_xlsx(x, name)
            except Exception:
                r = None
            if not r:
                bad.append(f"{name} (‘표제어’ 머리글이 있는 시트를 못 찾음)")
                continue
            words = r["words"][: spec.MAX_WORDS]
            wl = db.execute(select(WordList).where(WordList.academy_id == teacher.academy_id, WordList.is_base.is_(True),
                                                   WordList.title == r["title"])).scalars().first()
            if wl is None:
                wl = WordList(academy_id=teacher.academy_id, teacher_id=teacher.id, title=r["title"], is_base=True, source_kind="list")
                db.add(wl)
            wl.words, wl.level, wl.status, wl.error = words, r["level"], "done", None
            wl.source_text = "\n".join(w["word"] for w in words)
            done.append(f"{r['title']} ({len(words)}단어 · {len(r['days'])}일)")
    db.commit()
    if done:
        flash(request, "기본 단어장으로 올렸어요: " + ", ".join(done))
    if bad:
        flash(request, "못 올린 파일: " + ", ".join(bad), "error")
    if not done and not bad:
        flash(request, "파일을 골라 주세요.", "error")
    return _go("/vocab/base") if bad or not done else _go("/vocab")


@router.post("/{lid}/redays")
async def redays(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """기본 단어장: 단어 순서는 그대로 두고 Day 수만 다시 나눔 (예: 38일 → 40일, 앞 Day부터 하나씩 더)."""
    wl = _own(db, lid, teacher)
    f = await request.form()
    try:
        n = max(1, min(400, int(str(f.get("days") or "0"))))
    except ValueError:
        n = 0
    words = [dict(w) for w in wl.words]
    if not n or not words or n > len(words):
        flash(request, "Day 수를 확인해 주세요.", "error")
        return _go(f"/vocab/{lid}")
    base, extra = divmod(len(words), n)
    i = 0
    for d in range(n):
        for _ in range(base + (1 if d < extra else 0)):
            words[i]["src"] = f"Day {d + 1:02d}"
            i += 1
    wl.words = words
    db.commit()
    flash(request, f"{len(words)}단어를 {n}일로 다시 나눴어요 (하루 {base}{'~' + str(base + 1) if extra else ''}개).")
    return _go(f"/vocab/{lid}")


@router.post("/{lid}/base")
async def toggle_base(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """보통 단어장 ↔ 기본 단어장."""
    wl = _own(db, lid, teacher)
    wl.is_base = not wl.is_base
    db.commit()
    flash(request, "기본 단어장으로 옮겼어요." if wl.is_base else "기본 단어장에서 뺐어요 (단어장은 그대로 있어요).")
    return _go(f"/vocab/{lid}")


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    kind = "list" if f.get("kind") == "list" else "passage"
    title = str(f.get("title", "")).strip()[:120] or "단어장"
    level = str(f.get("level") or "")
    level = level if level in LEVELS else ""
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    ps = [{"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": str(x).replace("\r", "").strip()}
          for i, x in enumerate(texts) if str(x).strip()]
    passage = spec.join_passages(ps)
    listtext = str(f.get("listtext", "")).strip()
    try:
        count = max(2, min(60, int(f.get("count") or 10)))
    except ValueError:
        count = 10
    errors, words = [], []
    if kind == "passage":
        if not ps:
            errors.append("지문을 하나 이상 넣어 주세요.")
        if len(ps) > spec.MAX_PASSAGES:
            errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
        for i, p in enumerate(ps, 1):
            if len(p["text"]) > 8000:
                errors.append(f"지문 {i}이 너무 깁니다 (8000자까지).")
    else:
        words = [spec.clean_entry(w) for w in spec.parse_list(listtext)]
        if not words:
            errors.append("단어를 한 줄에 하나씩 넣어 주세요. 영어 단어가 하나도 없습니다.")
    needs_ai = kind == "passage" or any(spec.blank_needed(w) for w in words)
    fill = f.get("fill_ai") == "1" or kind == "passage"
    if needs_ai and fill and not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 넣거나, 'AI로 빈칸 채우기'를 꺼 주세요.")
    if errors:
        form = {"kind": kind, "title": title, "level": level, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)] or [{}],
                "listtext": listtext, "count": count, "fill_ai": fill}
        return render(request, "vocab/new.html", {"teacher": teacher, "form": form, "errors": errors, "levels": LEVELS, "max": spec.MAX_WORDS, "maxp": spec.MAX_PASSAGES, "active": "vocab"}, 400)

    wl = WordList(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, source_kind=kind,
                  source_text=passage, level=level, words=words, status="done" if not (needs_ai and fill) else "waiting")
    db.add(wl)
    db.commit()
    if needs_ai and fill:
        job = enqueue(db, academy_id=wl.academy_id, teacher_id=teacher.id, kind="vocab_build", title=f"단어장 · {title}", payload={"list_id": wl.id, "count": count})
        wl.last_job_id = job.id
        db.commit()
    if f.get("kind") != "list":
        remember_form(db, teacher, f)  # 지문 보관함에 저장
    return _go(f"/vocab/{wl.id}")


@router.get("/{lid}")
def detail(lid: int, request: Request, pdf_job: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    job = db.get(Job, wl.last_job_id) if wl.last_job_id else None
    classes = list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())
    assigned = {c.id for c in wl.classes}
    results = list(db.execute(
        select(QuizResult).options(selectinload(QuizResult.student)).where(QuizResult.list_id == wl.id).order_by(QuizResult.id.desc()).limit(100)
    ).scalars())
    # 많이 틀린 단어
    miss: dict[str, int] = {}
    for r in results:
        for w in r.wrong or []:
            miss[w["word"]] = miss.get(w["word"], 0) + 1
    top_miss = sorted(miss.items(), key=lambda kv: -kv[1])[:10]
    # 반에 열어 둔 경우: 반 학생 전원 현황 (안 푼 학생 포함)
    board = []
    if assigned:
        studs = list(db.execute(select(Student).options(selectinload(Student.class_group)).where(Student.class_id.in_(assigned), Student.active.is_(True))
                                .order_by(Student.class_id, Student.name)).scalars())
        allr = list(db.execute(select(QuizResult).where(QuizResult.list_id == wl.id)).scalars())
        for st in studs:
            mine = [r for r in allr if r.student_id == st.id]
            best = max(mine, key=lambda r: r.correct / max(1, r.total), default=None)
            board.append({"s": st, "n": len(mine), "best": best, "last": max((r.created_at for r in mine), default=None)})
    from ..homework import spec as hspec
    from ..models import Homework
    hws = [h for h in db.execute(select(Homework).options(selectinload(Homework.classes)).where(Homework.academy_id == teacher.academy_id)
                                 .order_by(Homework.id.desc()).limit(300)).scalars()
           if any(t.get("kind") == "vocab" and t.get("list_id") == wl.id for t in h.tasks or [])]
    from .homework import roster
    return render(request, "vocab/detail.html", {"roster": roster(db, teacher.academy_id), "board": board, "hws": hws, "stage_sets": hspec.STAGE_SETS,
        "teacher": teacher, "wl": wl, "job": job, "busy": _busy(db, wl), "classes": classes, "assigned": assigned,
        "results": results, "top_miss": top_miss, "modes": spec.QUIZ_MODES, "test_modes": spec.TEST_MODES,
        "sources": spec.sources(wl.words), "layouts": spec.LAYOUTS, "pdf_job": pdf_job, "active": "vocab",
        "blank_count": sum(1 for w in wl.words if spec.blank_needed(w)),
        "missing": spec.missing_passages(wl.source_text, wl.words) if wl.source_kind == "passage" else [],
    })


@router.post("/{lid}/words")
async def save_words(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    if _busy(db, wl):
        flash(request, "단어를 채우는 중에는 고칠 수 없어요.", "error")
        return _go(f"/vocab/{lid}")
    f = await request.form()
    cols = {k: f.getlist(k) for k in spec.FIELDS}
    removed = set(f.getlist("remove"))
    words, seen = [], set()
    for i in range(len(cols["word"])):
        e = spec.clean_entry({k: (cols[k][i] if i < len(cols[k]) else "") for k in spec.FIELDS})
        if not e["word"] or str(i) in removed or e["word"].lower() in seen:
            continue
        seen.add(e["word"].lower())
        words.append(e)
    wl.words = words[: spec.MAX_WORDS]
    title = str(f.get("title", "")).strip()[:120]
    if title:
        wl.title = title
    db.commit()
    if f.get("then") == "fill":
        return fill(lid, request, teacher, db)
    flash(request, f"단어 {len(wl.words)}개를 저장했어요.")
    return _go(f"/vocab/{lid}")


@router.post("/{lid}/missing")
def missing(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """단어가 안 뽑힌 지문만 다시 뽑기."""
    wl = _own(db, lid, teacher)
    if _busy(db, wl):
        return _go(f"/vocab/{lid}")
    if not teacher.gemini_key_enc:
        flash(request, "Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.", "error")
        return _go(f"/vocab/{lid}")
    if wl.source_kind != "passage" or not spec.missing_passages(wl.source_text, wl.words):
        flash(request, "모든 지문에서 단어를 뽑았어요.")
        return _go(f"/vocab/{lid}")
    job = enqueue(db, academy_id=wl.academy_id, teacher_id=teacher.id, kind="vocab_build", title=f"빠진 지문 단어 뽑기 · {wl.title}", payload={"list_id": wl.id, "missing": True})
    wl.last_job_id = job.id
    db.commit()
    return _go(f"/vocab/{lid}")


@router.post("/{lid}/fill")
def fill(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    if _busy(db, wl):
        return _go(f"/vocab/{lid}")
    if not teacher.gemini_key_enc:
        flash(request, "Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.", "error")
        return _go(f"/vocab/{lid}")
    if not any(spec.blank_needed(w) for w in wl.words):
        flash(request, "채울 빈칸이 없어요.")
        return _go(f"/vocab/{lid}")
    job = enqueue(db, academy_id=wl.academy_id, teacher_id=teacher.id, kind="vocab_build", title=f"단어장 빈칸 채우기 · {wl.title}", payload={"list_id": wl.id})
    wl.last_job_id = job.id
    db.commit()
    return _go(f"/vocab/{lid}")


@router.post("/{lid}/pdf")
async def pdf(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    f = await request.form()
    kind = "test" if f.get("kind") == "test" else "list"
    if not wl.words:
        flash(request, "단어가 없어요.", "error")
        return _go(f"/vocab/{lid}")
    if kind == "test":
        opts = _test_opts(wl, f.get("mode"), f.get("count"), f.get("per"), f.get("layout"), f.getlist("srcs"), f.get("pick_src") == "1", f)
        if opts is None:
            flash(request, "출제할 지문을 하나 이상 골라 주세요.", "error")
            return _go(f"/vocab/{lid}#pdf")
        payload = {"list_id": wl.id, "kind": "test", **opts, "shuffle": f.get("shuffle") == "1", "seed": secrets.randbits(31)}
        title = f"단어 시험지 · {wl.title}"
    else:
        hide = str(f.get("hide") or "")
        payload = {"list_id": wl.id, "kind": "list", "show_example": f.get("show_example") == "1", "hide": hide if hide in ("meaning", "word") else "",
                   "page_src": f.get("page_src") == "1"}
        title = f"단어장 PDF · {wl.title}"
    job = enqueue(db, academy_id=wl.academy_id, teacher_id=teacher.id, kind="vocab_pdf", title=title, payload=payload)
    return _go(f"/vocab/{lid}?pdf_job={job.id}#pdf")


def _num(v, default: int, hi: int) -> int:
    try:
        return max(0, min(hi, int(str(v).strip())))
    except (TypeError, ValueError):
        return default


def _test_opts(wl, mode, count, per, layout, srcs, pick_src, q=None):
    """시험지 옵션 정리. 지문을 하나도 안 고르면 None.
    q: 폼/쿼리 — 실전형 객관식·주관식 문항 수(mc, sa), 정답지(key: end 맨 뒤 / separate 따로 파일 / none 빼기)."""
    mode = str(mode or "en_ko")
    layout = layout if layout in spec.LAYOUTS else "together"
    have = spec.sources(wl.words)
    chosen = [s for s in have if s in set(srcs)] if pick_src else None
    if chosen is not None and not chosen:
        return None
    try:
        count = max(1, min(len(wl.words), int(count or len(wl.words))))
    except ValueError:
        count = len(wl.words)
    q = q or {}
    n = len(wl.words)
    key = str(q.get("key") or "end")
    return {"mode": mode if mode in spec.TEST_MODES else "en_ko", "count": count, "per_src": per == "src",
            "by_src": layout != "mixed", "layout": layout, "srcs": chosen,
            "mc": _num(q.get("mc"), 10, n), "sa": _num(q.get("sa"), 5, n), "key": key if key in ("end", "separate", "none") else "end"}


@router.get("/{lid}/docx")
def docx(lid: int, request: Request, kind: str = "list", hide: str = "", show_example: str = "", page_src: str = "", mode: str = "en_ko", count: str = "", shuffle: str = "",
         per: str = "", layout: str = "", pick_src: str = "",
         teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    if not wl.words:
        raise HTTPException(404, "단어가 없어요.")
    academy = db.get(Academy, wl.academy_id)
    if kind == "test":
        opts = _test_opts(wl, mode, count, per, layout, request.query_params.getlist("srcs"), pick_src == "1", request.query_params)
        if opts is None:
            flash(request, "출제할 지문을 하나 이상 골라 주세요.", "error")
            return _go(f"/vocab/{lid}#pdf")
        return vocab_test_docx(wl, academy, opts, shuffle == "1", secrets.randbits(31))
    return vocab_list_docx(wl, academy, hide if hide in ("meaning", "word") else "", show_example == "1", page_src == "1")


@router.post("/{lid}/classes")
async def assign(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    f = await request.form()
    ids = {int(x) for x in f.getlist("class_id") if str(x).isdigit()}
    valid = set(db.execute(select(ClassGroup.id).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.id.in_(ids))).scalars())
    db.execute(delete(WordListClass).where(WordListClass.list_id == wl.id))
    for cid in valid:
        db.add(WordListClass(list_id=wl.id, class_id=cid))
    db.commit()
    n = db.scalar(select(Student.id).where(Student.class_id.in_(valid)).limit(1)) if valid else None
    flash(request, f"{len(valid)}개 반에 내줬어요." + (" 학생들은 접속 코드로 들어가면 이 단어장을 볼 수 있어요." if n else "") if valid else "내준 반을 모두 뺐어요.")
    return _go(f"/vocab/{lid}#classes")


@router.post("/{lid}/delete")
def remove(lid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    if _busy(db, wl):
        flash(request, "단어를 채우는 중에는 지울 수 없어요.", "error")
        return _go(f"/vocab/{lid}")
    db.execute(delete(QuizResult).where(QuizResult.list_id == wl.id))
    db.execute(delete(WordListClass).where(WordListClass.list_id == wl.id))
    title = wl.title
    db.delete(wl)
    db.commit()
    flash(request, f"'{title}' 단어장과 퀴즈 기록을 지웠어요.")
    return _go("/vocab")
