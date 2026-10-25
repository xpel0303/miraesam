"""내용이해 워크북 화면."""
import copy
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..comprehension import spec
from ..db import get_db
from ..library.store import remember_form
from ..docx_builders import comprehension_docx
from ..jobs import enqueue
from ..models import Academy, Job, StoredFile, Teacher, Workbook
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/comprehension")

MAX_PASSAGE_CHARS = 6000
SPLITS = {"6:4": [6, 4], "7:3": [7, 3], "5:5": [5, 5]}


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, wid: int, teacher: Teacher) -> Workbook:
    wb = db.get(Workbook, wid)
    if wb is None or wb.academy_id != teacher.academy_id:
        raise HTTPException(404, "워크북을 찾을 수 없습니다.")
    return wb


def _busy(db: Session, wb: Workbook) -> Job | None:
    if not wb.last_job_id:
        return None
    job = db.get(Job, wb.last_job_id)
    return job if job and job.status in ("queued", "running") else None


def _start(db: Session, wb: Workbook, teacher: Teacher, kind: str, payload: dict, title: str) -> Job:
    job = enqueue(db, academy_id=wb.academy_id, teacher_id=teacher.id, kind=kind, title=title, payload={"workbook_id": wb.id, **payload})
    wb.last_job_id = job.id
    db.commit()
    return job


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    workbooks = list(
        db.execute(
            select(Workbook)
            .options(selectinload(Workbook.teacher))
            .where(Workbook.academy_id == teacher.academy_id)
            .order_by(Workbook.id.desc())
            .limit(100)
        ).scalars()
    )
    return render(request, "comprehension/index.html", {"teacher": teacher, "workbooks": workbooks, "active": "comprehension"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher)):
    return render(
        request,
        "comprehension/new.html",
        {"teacher": teacher, "form": {}, "max": spec.MAX_PASSAGES, "active": "comprehension"},
    )


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    titles, grammars, texts, kos = (f.getlist(k) for k in ("p_title", "p_grammar", "p_text", "p_ko"))
    common_grammar = str(f.get("common_grammar", "")).strip()

    passages, errors = [], []
    for i, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue
        if len(text) > MAX_PASSAGE_CHARS:
            errors.append(f"지문 {i + 1}이 너무 깁니다 ({len(text)}자). {MAX_PASSAGE_CHARS}자 이하로 나눠 주세요.")
        passages.append(
            {
                "title": str(titles[i]).strip()[:80] if i < len(titles) else "",
                "target_grammar": (str(grammars[i]).strip() if i < len(grammars) else "") or common_grammar,
                "passage": text,
                "passage_ko": str(kos[i]).strip() if i < len(kos) else "",
                "status": "waiting",
            }
        )
    if not passages:
        errors.append("지문을 하나 이상 넣어 주세요.")
    if len(passages) > spec.MAX_PASSAGES:
        errors.append(f"지문은 한 번에 {spec.MAX_PASSAGES}개까지입니다. 나머지는 워크북을 하나 더 만들어 주세요.")
    if not teacher.gemini_key_enc:
        errors.append("Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.")

    try:
        start_number = max(1, min(999, int(f.get("start_number") or 1)))
    except ValueError:
        start_number = 1
    options = {
        "start_number": start_number,
        "show_grammar": f.get("show_grammar") == "1",
        "show_translation": f.get("show_translation") == "1",
        "split": SPLITS.get(str(f.get("split")), [6, 4]),
    }
    title = str(f.get("title", "")).strip()[:120] or "내용이해 워크북"

    if errors:
        form = {
            "title": title,
            "common_grammar": common_grammar,
            "options": options,
            "split_key": str(f.get("split") or "6:4"),
            "passages": [{"title": t, "grammar": g, "text": x, "ko": k} for t, g, x, k in zip(titles, grammars, texts, kos)],
        }
        return render(request, "comprehension/new.html", {"teacher": teacher, "form": form, "errors": errors, "max": spec.MAX_PASSAGES, "active": "comprehension"}, 400)

    wb = Workbook(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, options=options, passages=passages)
    db.add(wb)
    db.commit()
    _start(db, wb, teacher, "comprehension_build", {}, f"내용이해 워크북 · {title}")
    remember_form(db, teacher, f)  # 지문 보관함에 저장
    return _go(f"/comprehension/{wb.id}")


@router.get("/{wid}")
def detail(wid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    job = db.get(Job, wb.last_job_id) if wb.last_job_id else None
    start = int((wb.options or {}).get("start_number", 1))
    return render(
        request,
        "comprehension/detail.html",
        {
            "teacher": teacher,
            "wb": wb,
            "job": job,
            "busy": job is not None and job.status in ("queued", "running"),
            "sections": spec.SECTIONS,
            "labels": spec.LABELS,
            "per": spec.PER_PASSAGE,
            "start": start,
            "all_done": all(p.get("status") == "done" for p in wb.passages),
            "active": "comprehension",
        },
    )


@router.post("/{wid}/passages/{idx}")
async def save_passage(wid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    if _busy(db, wb):
        flash(request, "만드는 중에는 고칠 수 없어요. 끝난 뒤에 고쳐 주세요.", "error")
        return _go(f"/comprehension/{wid}")
    if not 0 <= idx < len(wb.passages):
        raise HTTPException(404)
    f = await request.form()
    passages = copy.deepcopy(wb.passages)
    p = passages[idx]

    for field in ("title", "target_grammar", "passage", "passage_ko"):
        if field in f:
            p[field] = str(f.get(field, "")).strip()

    for key, n, _ in spec.SECTIONS:
        items = p.get(key) or []
        for i, x in enumerate(items):
            pre = f"{key}-{i}-"
            if key.startswith("ox_"):
                x["q"] = str(f.get(pre + "q", x.get("q", ""))).strip()
                a = str(f.get(pre + "a", x.get("a", ""))).strip().upper()
                x["a"] = a if a in ("O", "X") else x.get("a")
                x["why"] = str(f.get(pre + "why", x.get("why", ""))).strip()
            elif key == "compose":
                old_answer = x.get("answer", "")
                x["ko"] = str(f.get(pre + "ko", x.get("ko", ""))).strip()
                x["answer"] = " ".join(str(f.get(pre + "answer", old_answer)).split())
                x["point"] = str(f.get(pre + "point", x.get("point", ""))).strip()
                if x["answer"] != old_answer or f.get(pre + "reshuffle"):
                    x["given"] = spec.make_given(x["answer"], seed=secrets.randbits(32) if f.get(pre + "reshuffle") else None)
            elif key == "vocab":
                x["word"] = str(f.get(pre + "word", x.get("word", ""))).strip()
                x["meaning"] = str(f.get(pre + "meaning", x.get("meaning", ""))).strip()

    if p.get("status") == "done":
        _, p["warnings"] = spec.validate_passage(p)
    wb.passages = passages
    flag_modified(wb, "passages")
    opts = dict(wb.options or {})
    opts["dirty"] = True
    wb.options = opts
    db.commit()
    flash(request, f"{p.get('title') or f'지문 {idx + 1}'}을(를) 저장했어요. PDF에 반영하려면 'PDF 다시 만들기'를 눌러 주세요.")
    return _go(f"/comprehension/{wid}#p{idx}")


@router.get("/{wid}/docx")
def docx(wid: int, part: str = "problem", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    if not all(p.get("status") == "done" for p in wb.passages):
        raise HTTPException(404, "아직 다 만들어지지 않았어요.")
    return comprehension_docx(wb, db.get(Academy, wb.academy_id), answer=(part == "answer"))


@router.post("/{wid}/render")
def rerender(wid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    if not _busy(db, wb):
        opts = dict(wb.options or {})
        opts.pop("dirty", None)
        wb.options = opts
        _start(db, wb, teacher, "comprehension_render", {}, f"워크북 PDF 다시 만들기 · {wb.title}")
    return _go(f"/comprehension/{wid}")


@router.post("/{wid}/regenerate/{idx}")
def regenerate(wid: int, idx: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    if _busy(db, wb):
        flash(request, "이미 만드는 중이에요.", "error")
        return _go(f"/comprehension/{wid}")
    if not teacher.gemini_key_enc:
        flash(request, "Gemini API 키가 없습니다. 설정에서 키를 먼저 넣어 주세요.", "error")
        return _go(f"/comprehension/{wid}")
    if not 0 <= idx < len(wb.passages):
        raise HTTPException(404)
    passages = copy.deepcopy(wb.passages)
    passages[idx]["status"] = "waiting"
    wb.passages = passages
    flag_modified(wb, "passages")
    _start(db, wb, teacher, "comprehension_build", {"only": [idx]}, f"지문 {idx + 1} 다시 만들기 · {wb.title}")
    return _go(f"/comprehension/{wid}")


@router.post("/{wid}/retry")
def retry(wid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    if not _busy(db, wb):
        _start(db, wb, teacher, "comprehension_build", {}, f"내용이해 워크북 · {wb.title}")
    return _go(f"/comprehension/{wid}")


@router.post("/{wid}/delete")
def delete(wid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    wb = _own(db, wid, teacher)
    if _busy(db, wb):
        flash(request, "만드는 중에는 지울 수 없어요.", "error")
        return _go(f"/comprehension/{wid}")
    title = wb.title
    file_ids = [i for i in (wb.problem_file_id, wb.answer_file_id) if i]
    db.delete(wb)
    db.flush()
    for fid in file_ids:
        sf = db.get(StoredFile, fid)
        if sf is not None:
            db.delete(sf)
    db.commit()
    flash(request, f"'{title}' 워크북을 지웠어요.")
    return _go("/comprehension")
