"""문장 시험지 화면."""
import copy
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..library.store import remember_form
from ..docx_builders import sentence_docx
from ..handlers.sentence import sections_for
from ..jobs import enqueue
from ..models import Academy, Job, SentenceSet, StoredFile, Teacher
from ..sentence import spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/sentence")
LEVELS = ["", "중등", "고1", "고2", "고3"]
HINTS = {"count": "단어 수 알려 주기", "first": "첫 단어 알려 주기", "none": "힌트 없음"}


def _go(url):
    return RedirectResponse(url, status_code=303)


def _own(db, sid, teacher) -> SentenceSet:
    s = db.get(SentenceSet, sid)
    if s is None or s.academy_id != teacher.academy_id:
        raise HTTPException(404, "문장 시험지를 찾을 수 없습니다.")
    return s


def _busy(db, s) -> bool:
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    return bool(job and job.status in ("queued", "running"))


def _start(db, s, teacher, kind, title, payload=None):
    job = enqueue(db, academy_id=s.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"sentence_id": s.id, **(payload or {})})
    s.last_job_id = job.id
    db.commit()


def _read_options(f, old: dict | None = None) -> dict:
    types = [t for t in spec.TYPES if t in f.getlist("types")]
    hint = str(f.get("hint") or "count")
    return {**(old or {}), "types": types, "shuffle": f.get("shuffle") == "1", "hint": hint if hint in HINTS else "count"}


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    sets = list(db.execute(select(SentenceSet).options(selectinload(SentenceSet.teacher)).where(SentenceSet.academy_id == teacher.academy_id).order_by(SentenceSet.id.desc()).limit(100)).scalars())
    return render(request, "sentence/index.html", {"teacher": teacher, "sets": sets, "active": "sentence"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    form = {"options": {"types": list(spec.TYPES), "shuffle": True, "hint": "count"}, "passages": [{}]}
    return render(request, "sentence/new.html", {"teacher": teacher, "form": form, "spec": spec, "levels": LEVELS, "hints": HINTS, "active": "sentence"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, texts = f.getlist("p_title"), f.getlist("p_text")
    title = str(f.get("title", "")).strip()[:120] or "문장 시험지"
    level = str(f.get("level") or "")
    options = {**_read_options(f), "level": level if level in LEVELS else "", "seed": secrets.randbits(31)}
    passages, errors = [], []
    for i, x in enumerate(texts):
        x = str(x).strip()
        if not x:
            continue
        sents = spec.sentences_of(x)
        if len(sents) > spec.MAX_SENTENCES:
            errors.append(f"지문 {i + 1}이 {len(sents)}문장이에요. {spec.MAX_SENTENCES}문장 이하로 나눠 주세요.")
        passages.append({"title": str(titles[i]).strip()[:80] if i < len(titles) else "", "text": x, "sentences": sents, "status": "waiting"})
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다.")
    if not options["types"]:
        errors.append("유형을 하나 이상 골라 주세요.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")
    if errors:
        form = {"title": title, "level": level, "options": options, "passages": [{"title": t, "text": x} for t, x in zip(titles, texts)]}
        return render(request, "sentence/new.html", {"teacher": teacher, "form": form, "errors": errors, "spec": spec, "levels": LEVELS, "hints": HINTS, "active": "sentence"}, 400)
    s = SentenceSet(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(s)
    db.commit()
    _start(db, s, teacher, "sentence_build", f"문장 시험지 · {title}")
    remember_form(db, teacher, f)  # 지문 보관함에 저장
    return _go(f"/sentence/{s.id}")


@router.get("/{sid}")
def detail(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    job = db.get(Job, s.last_job_id) if s.last_job_id else None
    counts = {}
    if all(p.get("status") == "done" for p in s.passages):
        for g in sections_for(s):
            for sec in g["sections"]:
                counts[sec["type"]] = counts.get(sec["type"], 0) + len(sec["items"])
    return render(request, "sentence/detail.html", {"teacher": teacher, "s": s, "job": job, "busy": _busy(db, s), "spec": spec, "hints": HINTS,
                                                    "all_done": all(p.get("status") == "done" for p in s.passages), "counts": counts,
                                                    "opts": {"types": list(spec.TYPES), "shuffle": True, "hint": "count", **(s.options or {})}, "active": "sentence"})


@router.post("/{sid}/options")
async def save_options(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/sentence/{sid}")
    f = await request.form()
    opts = _read_options(f, s.options)
    if not opts["types"]:
        flash(request, "유형을 하나 이상 골라 주세요.", "error")
        return _go(f"/sentence/{sid}")
    if f.get("reshuffle"):
        opts["seed"] = secrets.randbits(31)
    opts.pop("dirty", None)
    s.options = opts
    if str(f.get("title", "")).strip():
        s.title = str(f.get("title")).strip()[:120]
    db.commit()
    if all(p.get("status") == "done" for p in s.passages):
        _start(db, s, teacher, "sentence_render", f"문장 시험지 PDF 다시 만들기 · {s.title}")
    return _go(f"/sentence/{sid}")


@router.post("/{sid}/passages/{idx}")
async def save_passage(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages):
        return _go(f"/sentence/{sid}")
    f = await request.form()
    ps = copy.deepcopy(s.passages)
    for r in ps[idx].get("rows") or []:
        pre = f"r{r['n']}-"
        r["ko"] = " ".join(str(f.get(pre + "ko", r["ko"])).split())
        keys = [k.strip() for k in str(f.get(pre + "keys", ", ".join(r["keys"]))).split(",") if k.strip()]
        r["keys"] = [k for k in keys if spec._find_word(r["en"], k)][:3]
    if f.get("title") is not None:
        ps[idx]["title"] = str(f.get("title")).strip()[:80]
    s.passages = ps
    flag_modified(s, "passages")
    s.options = {**s.options, "dirty": True}
    db.commit()
    flash(request, "저장했어요. 문장에 없는 빈칸 단어는 뺐어요. 'PDF 다시 만들기'를 눌러 반영하세요.")
    return _go(f"/sentence/{sid}#p{idx}")


@router.post("/{sid}/render")
def rerender(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        s.options = {k: v for k, v in s.options.items() if k != "dirty"}
        _start(db, s, teacher, "sentence_render", f"문장 시험지 PDF 다시 만들기 · {s.title}")
    return _go(f"/sentence/{sid}")


@router.post("/{sid}/regenerate/{idx}")
def regenerate(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s) and 0 <= idx < len(s.passages):
        ps = copy.deepcopy(s.passages)
        ps[idx]["status"] = "waiting"
        s.passages = ps
        flag_modified(s, "passages")
        _start(db, s, teacher, "sentence_build", f"지문 {idx + 1} 다시 만들기 · {s.title}", {"only": [idx]})
    return _go(f"/sentence/{sid}")


@router.post("/{sid}/drop/{idx}")
def drop(sid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """계속 실패하는 지문을 빼고 나머지로 시험지를 만들 수 있게."""
    s = _own(db, sid, teacher)
    if _busy(db, s) or not 0 <= idx < len(s.passages) or len(s.passages) == 1:
        return _go(f"/sentence/{sid}")
    ps = copy.deepcopy(s.passages)
    gone = ps.pop(idx)
    s.passages = ps
    flag_modified(s, "passages")
    db.commit()
    if all(p.get("status") == "done" for p in ps):
        _start(db, s, teacher, "sentence_render", f"문장 시험지 PDF 다시 만들기 · {s.title}")
    flash(request, f"'{gone.get('title') or f'지문 {idx + 1}'}'을(를) 뺐어요.")
    return _go(f"/sentence/{sid}")


@router.post("/{sid}/retry")
def retry(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not _busy(db, s):
        _start(db, s, teacher, "sentence_build", f"문장 시험지 · {s.title}")
    return _go(f"/sentence/{sid}")


@router.get("/{sid}/docx")
def docx(sid: int, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if not all(p.get("status") == "done" for p in s.passages):
        raise HTTPException(404, "아직 다 만들어지지 않았어요.")
    return sentence_docx(s, db.get(Academy, s.academy_id), sections_for(s))


@router.post("/{sid}/delete")
def remove(sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    s = _own(db, sid, teacher)
    if _busy(db, s):
        return _go(f"/sentence/{sid}")
    title, fid = s.title, s.file_id
    db.delete(s)
    db.flush()
    if fid and (sf := db.get(StoredFile, fid)) is not None:
        db.delete(sf)
    db.commit()
    flash(request, f"'{title}'을(를) 지웠어요.")
    return _go("/sentence")
