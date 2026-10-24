"""기출 분석 작업.

exam_extract : 올린 파일 → 쪽 이미지(방향 보정) → 2쪽씩 Gemini 로 문항 옮기기 → 합치기·검사
               쪽 묶음마다 저장하므로 서버가 재시작돼도 끝난 묶음은 건너뜀.
exam_pdf     : 분석지 PDF
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete

from .. import llm
from ..db import session_scope
from ..exam import pages as pg
from ..exam import spec
from ..jobs import JobContext, job_handler
from ..models import Academy, ExamPaper, StoredFile
from ..pdf import render_pdf


def _set(pid: int, **fields):
    with session_scope() as db:
        p = db.get(ExamPaper, pid)
        for k, v in fields.items():
            setattr(p, k, v)


def _ask(key, model, images, prompt_text) -> dict:
    """한 묶음 요청. JSON 모양이 틀리면 한 번 더."""
    last = None
    for attempt in range(2):
        raw = llm.generate(key, prompt_text + ("" if attempt == 0 else "\n\n직전 답이 형식에 맞지 않았습니다. 위 형식의 JSON 객체 하나로만 다시 답해 주세요."),
                           system=spec.SYSTEM_PROMPT, images=images, model=model, json_mode=True, temperature=0.1, max_tokens=24000)
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            return raw
        if isinstance(raw, list):
            return {"header": None, "items": raw}
        last = raw
    raise llm.LLMError(f"시험지 내용을 형식에 맞게 받지 못했습니다. ({type(last).__name__})")


@job_handler("exam_extract")
def extract(ctx: JobContext) -> dict:
    pid = ctx.payload["paper_id"]
    key, model = ctx.gemini()
    _set(pid, status="making", error=None)
    try:
        # 1) 쪽 이미지
        with session_scope() as db:
            p = db.get(ExamPaper, pid)
            have_pages = bool(p.pages)
            upload_ids = list((p.meta or {}).get("upload_ids") or [])
        if not have_pages:
            ctx.step("시험지를 쪽 이미지로 바꾸는 중", 3)
            with session_scope() as db:
                files = [(f.filename, f.data) for f in (db.get(StoredFile, i) for i in upload_ids) if f]
            if not files:
                raise llm.LLMError("올린 파일을 찾을 수 없어요. 시험지를 다시 올려 주세요.")
            try:
                prepared = pg.prepare(files, ctx.step)
            except ValueError as e:
                raise llm.LLMError(str(e))
            except Exception as e:  # 깨진 PDF 등
                raise llm.LLMError(f"시험지 파일을 읽지 못했어요 ({type(e).__name__}). PDF나 사진 파일이 맞는지 확인해 주세요.")
            page_rows = []
            for i, x in enumerate(prepared, 1):
                fid = ctx.save_file(f"page_{i}.jpg", "image/jpeg", x["jpeg"])
                page_rows.append({"file_id": fid, "rot": x["rot"], "w": x["w"], "h": x["h"], "blank": x["blank"]})
            with session_scope() as db:
                p = db.get(ExamPaper, pid)
                p.pages = page_rows
                p.meta = {k: v for k, v in (p.meta or {}).items() if k != "upload_ids"}
                db.execute(delete(StoredFile).where(StoredFile.id.in_(upload_ids)))  # 원본(수십 MB)은 지움

        # 2) 2쪽씩 Gemini
        with session_scope() as db:
            p = db.get(ExamPaper, pid)
            page_rows, chunks = list(p.pages), dict(p.chunks or {})
        n = len(page_rows)
        starts = list(range(0, n, spec.CHUNK))
        for k, start in enumerate(starts):
            if str(start) in chunks:
                continue
            idx = [i for i in range(start, min(n, start + spec.CHUNK)) if not page_rows[i].get("blank")]
            ctx.step(f"{start + 1}~{min(n, start + spec.CHUNK)}쪽 문항 읽는 중 ({k + 1}/{len(starts)})", 30 + int(60 * k / len(starts)))
            if not idx:
                chunks[str(start)] = {"header": None, "items": []}
            else:
                with session_scope() as db:
                    imgs = [llm.Image(db.get(StoredFile, page_rows[i]["file_id"]).data, "image/jpeg") for i in idx]
                prev = spec.last_label(chunks.get(str(start - spec.CHUNK))) if start else ""
                res = _ask(key, model, imgs, spec.prompt([i + 1 for i in idx], n, prev))
                res["_pages"] = [i + 1 for i in idx]  # 이미지 순서 → 실제 쪽 번호
                chunks[str(start)] = res
            _set(pid, chunks=dict(chunks))

        # 3) 합치기·검사
        ctx.step("문항 합치고 검사하는 중", 94)
        qs, meta = spec.merge(chunks)
        if not qs:
            raise llm.LLMError("문항을 하나도 찾지 못했어요. 시험지 이미지가 흐리거나 문항이 없는 쪽만 올렸는지 확인해 주세요.")
        warns = spec.validate(qs, n) + spec.check_counts(qs, meta)
        with session_scope() as db:
            p = db.get(ExamPaper, pid)
            p.questions = qs
            p.meta = {**(p.meta or {}), **{f"read_{k}": v for k, v in meta.items()}}
            p.warnings = warns
            p.status = "done"
            auto = p.title == auto_title(p)
            _fill_from_header(p, meta, db)
            if auto:
                p.title = auto_title(p)
        return {"paper_id": pid, "questions": len(qs)}
    except llm.LLMError as e:
        _set(pid, status="failed", error=str(e))
        raise


def auto_title(p: ExamPaper) -> str:
    parts = [p.school.name if p.school else "", p.grade, f"{p.year}" if p.year else "", f"{p.semester}학기" if p.semester else "",
             f"{p.term}차" if p.term else ""]
    return " ".join(x for x in parts if x) or "기출 분석"


def short_school(name: str) -> str:
    """'전라고등학교' → '전라고', '전주중앙여자고등학교' → '전주중앙여고', '전북대학교사범대학부설고등학교' → 그대로 줄임."""
    n = re.sub(r"\s+", "", name or "")
    n = n.replace("대학교사범대학부설", "대사대부")
    n = n.replace("여자고등학교", "여고").replace("고등학교", "고").replace("여자중학교", "여중").replace("중학교", "중")
    return n[:40]


def _fill_from_header(p: ExamPaper, meta: dict, db=None):
    """선생님이 비워 둔 칸만 시험지 머리말로 채움. 학교는 이미 있는 학교와 이름이 맞으면 연결, 없으면 새로 만듦."""
    if not p.school_id and db is not None and meta.get("school"):
        from ..models import School
        short = short_school(meta["school"])
        cands = list(db.execute(__import__("sqlalchemy").select(School).where(School.academy_id == p.academy_id)).scalars())
        hit = next((s for s in cands if s.name in (short, meta["school"]) or short_school(s.name) == short), None)
        if hit is None and short:
            hit = School(academy_id=p.academy_id, name=short, level="중" if short.endswith("중") else "고")
            db.add(hit)
            db.flush()
        if hit:
            p.school_id = hit.id
            p.school = hit
    def num(v):
        m = re.search(r"\d+", str(v or ""))
        return int(m.group()) if m else None
    if not p.year and num(meta.get("year")) and 2000 < num(meta.get("year")) < 2100:
        p.year = num(meta.get("year"))
    if not p.semester and num(meta.get("semester")) in (1, 2):
        p.semester = num(meta.get("semester"))
    if not p.term and num(meta.get("term")) in (1, 2):
        p.term = num(meta.get("term"))
    if not p.grade and num(meta.get("grade")) in (1, 2, 3):
        p.grade = f"고{num(meta.get('grade'))}"


@job_handler("exam_pdf")
def make_pdf(ctx: JobContext) -> dict:
    pid = ctx.payload["paper_id"]
    with session_scope() as db:
        p = db.get(ExamPaper, pid)
        academy = db.get(Academy, p.academy_id)
        db.expunge(academy)
        qs = [dict(q) for q in p.questions]
        info = {"title": p.title, "school": p.school.name if p.school else "", "grade": p.grade, "year": p.year,
                "semester": p.semester, "term": p.term, "date": (p.meta or {}).get("read_date", "")}
        old = p.file_id
    ctx.step("분석지 만드는 중", 50)
    st = spec.stats(qs)
    pdf = render_pdf("pdf/exam_analysis.html", {"academy": academy, "p": info, "qs": qs, "st": st, "notes": spec.notes(qs, st),
                                                "doc_title": f"{info['title']} · 기출 분석",
                                                "today": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")})
    name = f"{info['title']}_기출분석.pdf"
    fid = ctx.save_file(name, "application/pdf", pdf)
    with session_scope() as db:
        p = db.get(ExamPaper, pid)
        p.file_id = fid
        if old:
            db.execute(delete(StoredFile).where(StoredFile.id == old))
    return {"file_id": fid, "filename": name}
