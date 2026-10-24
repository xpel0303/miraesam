"""단어장 (선생님 화면)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
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
        .where(WordList.academy_id == teacher.academy_id).order_by(WordList.id.desc()).limit(200)
    ).scalars())
    return render(request, "vocab/index.html", {"teacher": teacher, "lists": lists, "active": "vocab"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(request, "vocab/new.html", {"teacher": teacher, "form": {"kind": "passage", "count": 20}, "levels": LEVELS, "max": spec.MAX_WORDS, "active": "vocab"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    kind = "list" if f.get("kind") == "list" else "passage"
    title = str(f.get("title", "")).strip()[:120] or "단어장"
    level = str(f.get("level") or "")
    level = level if level in LEVELS else ""
    passage = str(f.get("passage", "")).strip()
    listtext = str(f.get("listtext", "")).strip()
    try:
        count = max(5, min(60, int(f.get("count") or 20)))
    except ValueError:
        count = 20
    errors, words = [], []
    if kind == "passage":
        if not passage:
            errors.append("지문을 넣어 주세요.")
        elif len(passage) > 8000:
            errors.append("지문이 너무 깁니다. 8000자 이하로 나눠 주세요.")
    else:
        words = [spec.clean_entry(w) for w in spec.parse_list(listtext)]
        if not words:
            errors.append("단어를 한 줄에 하나씩 넣어 주세요. 영어 단어가 하나도 없습니다.")
    needs_ai = kind == "passage" or any(spec.blank_needed(w) for w in words)
    fill = f.get("fill_ai") == "1" or kind == "passage"
    if needs_ai and fill and not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 넣거나, 'AI로 빈칸 채우기'를 꺼 주세요.")
    if errors:
        form = {"kind": kind, "title": title, "level": level, "passage": passage, "listtext": listtext, "count": count, "fill_ai": fill}
        return render(request, "vocab/new.html", {"teacher": teacher, "form": form, "errors": errors, "levels": LEVELS, "max": spec.MAX_WORDS, "active": "vocab"}, 400)

    wl = WordList(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, source_kind=kind,
                  source_text=passage, level=level, words=words, status="done" if not (needs_ai and fill) else "waiting")
    db.add(wl)
    db.commit()
    if needs_ai and fill:
        job = enqueue(db, academy_id=wl.academy_id, teacher_id=teacher.id, kind="vocab_build", title=f"단어장 · {title}", payload={"list_id": wl.id, "count": count})
        wl.last_job_id = job.id
        db.commit()
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
    return render(request, "vocab/detail.html", {
        "teacher": teacher, "wl": wl, "job": job, "busy": _busy(db, wl), "classes": classes, "assigned": assigned,
        "results": results, "top_miss": top_miss, "modes": spec.QUIZ_MODES, "pdf_job": pdf_job, "active": "vocab",
        "blank_count": sum(1 for w in wl.words if spec.blank_needed(w)),
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
        try:
            count = max(1, min(len(wl.words), int(f.get("count") or len(wl.words))))
        except ValueError:
            count = len(wl.words)
        mode = str(f.get("mode") or "en_ko")
        payload = {"list_id": wl.id, "kind": "test", "mode": mode if mode in ("en_ko", "ko_en", "mix") else "en_ko",
                   "count": count, "shuffle": f.get("shuffle") == "1", "seed": secrets.randbits(31)}
        title = f"단어 시험지 · {wl.title}"
    else:
        hide = str(f.get("hide") or "")
        payload = {"list_id": wl.id, "kind": "list", "show_example": f.get("show_example") == "1", "hide": hide if hide in ("meaning", "word") else ""}
        title = f"단어장 PDF · {wl.title}"
    job = enqueue(db, academy_id=wl.academy_id, teacher_id=teacher.id, kind="vocab_pdf", title=title, payload=payload)
    return _go(f"/vocab/{lid}?pdf_job={job.id}#pdf")


@router.get("/{lid}/docx")
def docx(lid: int, kind: str = "list", hide: str = "", show_example: str = "", mode: str = "en_ko", count: int = 0, shuffle: str = "",
         teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wl = _own(db, lid, teacher)
    if not wl.words:
        raise HTTPException(404, "단어가 없어요.")
    academy = db.get(Academy, wl.academy_id)
    if kind == "test":
        return vocab_test_docx(wl, academy, mode if mode in ("en_ko", "ko_en", "mix") else "en_ko",
                               max(1, min(len(wl.words), count or len(wl.words))), shuffle == "1", secrets.randbits(31))
    return vocab_list_docx(wl, academy, hide if hide in ("meaning", "word") else "", show_example == "1")


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
