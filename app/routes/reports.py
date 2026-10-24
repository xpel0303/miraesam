"""학생·반 리포트 (선생님 화면).

시험 만들기(정답·배점·유형) → 답 입력(선생님이 한 줄씩, 또는 학생이 접속 코드로 제출)
→ 자동 채점·통계 → 학생별 리포트 PDF / 반 리포트 PDF·이미지 / 성적 엑셀(CSV).
"""
import csv
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..jobs import enqueue
from ..models import ClassGroup, Exam, ExamClass, ExamResponse, School, Student, Teacher
from ..reports import data, spec
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/reports")
TABS = {"items": "문항·정답", "answers": "답 입력", "results": "결과", "share": "학생 제출·리포트"}


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, eid: int, teacher: Teacher) -> Exam:
    e = db.get(Exam, eid)
    if e is None or e.academy_id != teacher.academy_id:
        raise HTTPException(404, "시험을 찾을 수 없습니다.")
    return e


def _classes(db: Session, teacher: Teacher) -> list[ClassGroup]:
    return list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())


def _schools(db: Session, teacher: Teacher) -> list[School]:
    return list(db.execute(select(School).where(School.academy_id == teacher.academy_id).order_by(School.name)).scalars())


def _set_classes(db: Session, exam: Exam, teacher: Teacher, raw: list) -> int:
    ids = {int(x) for x in raw if str(x).isdigit()}
    valid = set(db.execute(select(ClassGroup.id).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.id.in_(ids))).scalars()) if ids else set()
    db.execute(delete(ExamClass).where(ExamClass.exam_id == exam.id))
    for cid in valid:
        db.add(ExamClass(exam_id=exam.id, class_id=cid))
    return len(valid)


def _date(v) -> str:
    s = str(v or "").strip()
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else ""


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    exams = list(db.execute(
        select(Exam).options(selectinload(Exam.classes), selectinload(Exam.school), selectinload(Exam.teacher))
        .where(Exam.academy_id == teacher.academy_id).order_by(Exam.exam_date.desc(), Exam.id.desc()).limit(200)
    ).scalars())
    counts = {}
    for eid, in db.execute(select(ExamResponse.exam_id).where(ExamResponse.academy_id == teacher.academy_id)):
        counts[eid] = counts.get(eid, 0) + 1
    return render(request, "reports/index.html", {"teacher": teacher, "exams": exams, "counts": counts, "active": "reports"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    return render(request, "reports/new.html", {"teacher": teacher, "form": {"mc": 25, "sa": 0, "date": today}, "classes": _classes(db, teacher),
                                                "schools": _schools(db, teacher), "active": "reports"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    title = str(f.get("title", "")).strip()[:120]
    try:
        mc, sa = int(f.get("mc") or 0), int(f.get("sa") or 0)
    except ValueError:
        mc, sa = -1, -1
    key = spec.parse_key(str(f.get("key", "")))
    mc_pts = spec.num(f.get("mc_points"), -1) if str(f.get("mc_points", "")).strip() else None
    sa_pts = spec.num(f.get("sa_points"), -1) if str(f.get("sa_points", "")).strip() else None
    errors = []
    if not title:
        errors.append("시험 이름을 넣어 주세요.")
    if mc < 0 or sa < 0 or mc + sa == 0:
        errors.append("문항 수를 넣어 주세요.")
    elif mc + sa > spec.MAX_ITEMS:
        errors.append(f"문항은 {spec.MAX_ITEMS}개까지예요.")
    if (mc_pts is not None and mc_pts < 0) or (sa_pts is not None and sa_pts < 0):
        errors.append("배점은 숫자로 넣어 주세요.")
    if key and len(key) != mc and mc > 0:
        errors.append(f"정답이 {len(key)}개인데 객관식은 {mc}문항이에요. 개수를 맞춰 주세요. (비워 두고 다음 화면에서 넣어도 됩니다)")
    school_id = int(f["school_id"]) if str(f.get("school_id", "")).isdigit() else None
    if school_id and not db.execute(select(School.id).where(School.id == school_id, School.academy_id == teacher.academy_id)).first():
        school_id = None
    if errors:
        form = {k: f.get(k, "") for k in ("title", "key", "mc_points", "sa_points", "school_id")}
        form.update({"mc": f.get("mc"), "sa": f.get("sa"), "date": f.get("date"), "class_ids": {int(x) for x in f.getlist("class_id") if str(x).isdigit()}})
        return render(request, "reports/new.html", {"teacher": teacher, "form": form, "errors": errors, "classes": _classes(db, teacher),
                                                    "schools": _schools(db, teacher), "active": "reports"}, 400)
    e = Exam(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, school_id=school_id, exam_date=_date(f.get("date")),
             items=spec.make_items(mc, sa, key, mc_pts, sa_pts), options={"open": False, "reveal": True})
    db.add(e)
    db.flush()
    _set_classes(db, e, teacher, f.getlist("class_id"))
    db.commit()
    flash(request, "시험을 만들었어요. 유형과 출처를 넣으면 리포트가 더 자세해집니다.")
    return _go(f"/reports/{e.id}?tab=items")


@router.get("/{eid}")
def detail(eid: int, request: Request, tab: str = "", class_id: str = "", pdf_job: int | None = None,
           teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    cid = int(class_id) if class_id.isdigit() else None
    S, students, responses = data.summary(db, e, cid)
    items = spec.clean_items(e.items or [])
    if tab not in TABS:
        tab = "results" if S["n"] else ("answers" if all(it["answer"] or it["kind"] == "sa" for it in items) else "items")
    mc_items = [it for it in items if it["kind"] == "mc"]
    sa_items = [it for it in items if it["kind"] == "sa"]
    graded = {p["id"]: p for p in S["people"]}
    rows = []
    for s in students:
        r = responses.get(s.id)
        rows.append({"s": s, "r": r, "line": spec.row_text(r.answers or {}, mc_items) if r else "", "g": graded.get(s.id)})
    return render(request, "reports/detail.html", {
        "teacher": teacher, "e": e, "tab": tab, "tabs": TABS, "items": items, "mc_items": mc_items, "sa_items": sa_items,
        "warnings": spec.check_items(items), "S": S, "rows": rows, "types": spec.TYPES, "fmt": spec.fmt, "circ": spec.CIRCLED,
        "classes": _classes(db, teacher), "assigned": {c.id for c in e.classes}, "schools": _schools(db, teacher),
        "class_id": cid, "pdf_job": pdf_job, "active": "reports", "easy": spec.EASY_MISS,
    })


@router.post("/{eid}/items")
async def save_items(eid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    f = await request.form()
    cols = {k: f.getlist(k) for k in ("kind", "answer", "points", "type", "source")}
    removed = set(f.getlist("remove"))
    items = []
    for i in range(len(cols["kind"])):
        if str(i) in removed:
            continue
        items.append({k: (cols[k][i] if i < len(cols[k]) else "") for k in cols})
    old = spec.clean_items(e.items or [])
    new_items = spec.clean_items(items)
    # 문항을 빼면 번호가 당겨지므로 저장된 답의 번호도 같이 옮김
    if removed:
        keep = [i for i in range(len(cols["kind"])) if str(i) not in removed]
        remap = {str(old_i + 1): str(new_i + 1) for new_i, old_i in enumerate(keep)}
        for r in db.execute(select(ExamResponse).where(ExamResponse.exam_id == e.id)).scalars():
            r.answers = {remap[k]: v for k, v in (r.answers or {}).items() if k in remap}
            r.sa_scores = {remap[k]: v for k, v in (r.sa_scores or {}).items() if k in remap}
            r.sa_text = {remap[k]: v for k, v in (r.sa_text or {}).items() if k in remap}
    add_mc, add_sa = int(f.get("add_mc") or 0) if str(f.get("add_mc", "")).isdigit() else 0, int(f.get("add_sa") or 0) if str(f.get("add_sa", "")).isdigit() else 0
    for kind, n in (("mc", add_mc), ("sa", add_sa)):
        last = next((it for it in reversed(new_items) if it["kind"] == kind), None)
        new_items += [{"kind": kind, "answer": "", "points": last["points"] if last else 0, "type": "", "source": ""} for _ in range(n)]
    new_items = spec.clean_items(new_items)
    key = spec.parse_key(str(f.get("bulk_key", "")))
    if key:
        mcs = [it for it in new_items if it["kind"] == "mc"]
        for it, k in zip(mcs, key):
            it["answer"] = k
    bulk_type = str(f.get("bulk_types", "")).strip()
    if bulk_type:  # '1-3 어법, 4 빈칸' 형식
        for part in re.split(r"[,\n]+", bulk_type):
            m = re.match(r"\s*(\d+)\s*(?:[-~]\s*(\d+))?\s*(.+)", part)
            if m:
                a, b = int(m.group(1)), int(m.group(2) or m.group(1))
                for it in new_items:
                    if a <= it["n"] <= b:
                        it["type"] = m.group(3).strip()[:20]
    e.items = new_items
    e.title = str(f.get("title", "")).strip()[:120] or e.title
    e.exam_date = _date(f.get("date")) or e.exam_date
    sid = str(f.get("school_id", ""))
    e.school_id = int(sid) if sid.isdigit() and db.execute(select(School.id).where(School.id == int(sid), School.academy_id == teacher.academy_id)).first() else None
    db.commit()
    changed = [str(n["n"]) for n, o in zip(new_items, old) if n["kind"] == o["kind"] and (n["answer"] != o["answer"] or n["points"] != o["points"])]
    msg = f"문항 {len(new_items)}개를 저장했어요."
    if changed and db.execute(select(ExamResponse.id).where(ExamResponse.exam_id == e.id)).first():
        msg += f" 정답·배점이 바뀐 {', '.join(changed[:10])}번은 학생 점수에 바로 다시 반영됐어요."
    flash(request, msg)
    return _go(f"/reports/{eid}?tab=items")


@router.post("/{eid}/answers")
async def save_answers(eid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    f = await request.form()
    items = spec.clean_items(e.items or [])
    mc_items = [it for it in items if it["kind"] == "mc"]
    sa_items = [it for it in items if it["kind"] == "sa"]
    students, responses = data.roster(db, e)
    allowed = {s.id for s in students}
    saved, cleared, too_long = 0, 0, []
    for sid in [int(x) for x in f.getlist("sid") if str(x).isdigit()]:
        if sid not in allowed:
            continue
        raw = str(f.get(f"mc_{sid}", ""))
        vals = spec.parse_row(raw, len(mc_items))
        if len(spec.parse_key(raw)) > len(mc_items) or re.search(r"[6-9]", raw):
            too_long.append(sid)
        answers = {str(it["n"]): v for it, v in zip(mc_items, vals) if v}
        scores = {}
        for it in sa_items:
            v = str(f.get(f"sa_{sid}_{it['n']}", "")).strip()
            if v != "":
                scores[str(it["n"])] = min(it["points"], spec.num(v))
        r = responses.get(sid)
        if r is None:
            if not answers and not scores:
                continue
            r = ExamResponse(academy_id=e.academy_id, exam_id=e.id, student_id=sid, by="teacher")
            db.add(r)
        if r.answers != answers or r.sa_scores != scores:
            if not answers and not scores and not r.comment and not r.sa_text:
                db.delete(r)
                cleared += 1
                continue
            r.answers, r.sa_scores = answers, scores
            saved += 1
    db.commit()
    names = {s.id: s.name for s in students}
    msg = f"{saved}명 답을 저장했어요." + (f" {cleared}명은 답을 모두 지워서 응시 기록을 뺐어요." if cleared else "")
    if too_long:
        msg += f" 확인해 주세요 — {', '.join(names[i] for i in too_long[:5])}: 문항 수보다 많이 넣었거나 1~5가 아닌 숫자가 있어서 그 부분은 버렸어요."
    flash(request, msg, "error" if too_long else "ok")
    return _go(f"/reports/{eid}?tab=answers")


@router.post("/{eid}/settings")
async def save_settings(eid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    f = await request.form()
    n = _set_classes(db, e, teacher, f.getlist("class_id"))
    e.options = {**(e.options or {}), "open": f.get("open") == "1", "reveal": f.get("reveal") == "1"}
    db.commit()
    if e.options["open"] and not n:
        flash(request, "답 제출을 열었지만 내준 반이 없어요. 반을 골라야 학생 화면에 보입니다.", "error")
    else:
        flash(request, "저장했어요." + (" 학생이 접속 코드로 들어가면 이 시험 답 입력 칸이 보여요." if e.options["open"] else ""))
    return _go(f"/reports/{eid}?tab=share")


@router.get("/{eid}/s/{sid}")
def student_page(eid: int, sid: int, request: Request, pdf_job: int | None = None, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    S, students, responses = data.summary(db, e)
    st = next((s for s in students if s.id == sid), None)
    if st is None:
        raise HTTPException(404, "이 시험 대상 학생이 아니에요.")
    items = spec.clean_items(e.items or [])
    r = responses.get(sid)
    view = spec.student_view(S, sid) if r else None
    ids = [p["id"] for p in S["people"]] if S["people"] else []
    order = [s.id for s in students]
    i = order.index(sid)
    return render(request, "reports/student.html", {
        "teacher": teacher, "e": e, "st": st, "r": r, "items": items, "view": view, "S": S, "fmt": spec.fmt, "circ": spec.CIRCLED,
        "prev": order[i - 1] if i > 0 else None, "next": order[i + 1] if i + 1 < len(order) else None, "pdf_job": pdf_job,
        "active": "reports", "has_results": bool(ids),
    })


@router.post("/{eid}/s/{sid}")
async def save_student(eid: int, sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    students, responses = data.roster(db, e)
    if sid not in {s.id for s in students}:
        raise HTTPException(404)
    f = await request.form()
    items = spec.clean_items(e.items or [])
    answers, scores, texts = {}, {}, {}
    for it in items:
        k = str(it["n"])
        if it["kind"] == "mc":
            v = spec.norm_mc(f.get(f"a{k}", ""))
            if v:
                answers[k] = v
        else:
            v = str(f.get(f"s{k}", "")).strip()
            if v != "":
                scores[k] = min(it["points"], spec.num(v))
            t = str(f.get(f"t{k}", "")).strip()[:1000]
            if t:
                texts[k] = t
    comment = str(f.get("comment", "")).strip()[:2000]
    r = responses.get(sid)
    if r is None:
        if not (answers or scores or comment or texts):
            return _go(f"/reports/{eid}/s/{sid}")
        r = ExamResponse(academy_id=e.academy_id, exam_id=e.id, student_id=sid, by="teacher")
        db.add(r)
    r.answers, r.sa_scores, r.sa_text, r.comment = answers, scores, texts, comment
    db.commit()
    flash(request, "저장했어요.")
    nxt = f.get("then")
    if nxt and str(nxt).isdigit():
        return _go(f"/reports/{eid}/s/{nxt}")
    return _go(f"/reports/{eid}/s/{sid}")


@router.post("/{eid}/s/{sid}/clear")
def clear_student(eid: int, sid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    db.execute(delete(ExamResponse).where(ExamResponse.exam_id == e.id, ExamResponse.student_id == sid))
    db.commit()
    flash(request, "이 학생의 답안과 코멘트를 지웠어요.")
    return _go(f"/reports/{eid}?tab=answers")


@router.post("/{eid}/pdf")
async def pdf(eid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    f = await request.form()
    kind = str(f.get("kind", "class"))
    if kind not in ("class", "students", "one", "image"):
        raise HTTPException(400)
    cid = int(f["class_id"]) if str(f.get("class_id", "")).isdigit() else None
    sid = int(f["student_id"]) if str(f.get("student_id", "")).isdigit() else None
    S, _, _ = data.summary(db, e, cid)
    if not S["n"]:
        flash(request, "아직 답을 입력한 학생이 없어요.", "error")
        return _go(f"/reports/{eid}?tab=share")
    payload = {"exam_id": e.id, "kind": kind, "class_id": cid, "student_id": sid,
               "mask": f.get("mask") == "1", "show_rank": f.get("show_rank") == "1", "show_names": f.get("show_names") == "1"}
    names = {"class": "반 리포트", "students": "학생별 리포트", "one": "학생 리포트", "image": "결과 이미지"}
    job = enqueue(db, academy_id=e.academy_id, teacher_id=teacher.id, kind="report_pdf", title=f"{names[kind]} · {e.title}", payload=payload)
    if kind == "one" and sid:
        return _go(f"/reports/{eid}/s/{sid}?pdf_job={job.id}#pdf")
    return _go(f"/reports/{eid}?tab=share&pdf_job={job.id}" + (f"&class_id={cid}" if cid else "") + "#pdf")


@router.get("/{eid}/csv")
def export_csv(eid: int, class_id: str = "", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    S, _, _ = data.summary(db, e, int(class_id) if class_id.isdigit() else None)
    items = spec.clean_items(e.items or [])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["등수", "이름", "반", "학교", "총점", "객관식 맞은 수", "서술형 점수"] + [f"{it['n']}번" for it in items])
    for p in S["people"]:
        cells = []
        for row in p["rows"]:
            if row["kind"] == "mc":
                cells.append(("O " if row["ok"] else "X ") + (row["got"] or "-"))
            else:
                cells.append(spec.fmt(row["earned"]) if row["graded"] else "미채점")
        w.writerow([p["rank"], p["name"], p["class_name"], p["school"], spec.fmt(p["score"]), p["mc_correct"], spec.fmt(p["sa_score"])] + cells)
    w.writerow([])
    w.writerow(["", "정답", "", "", "", "", ""] + [("/".join(spec.CIRCLED[c] for c in it["answer"].split("/")) if it["kind"] == "mc" and it["answer"] else "서술형") for it in items])
    w.writerow(["", "정답률(%)", "", "", "", "", ""] + [("" if it["rate"] is None else it["rate"]) for it in S["items"]])
    body = "﻿" + buf.getvalue()  # 엑셀에서 한글이 깨지지 않게 BOM
    from urllib.parse import quote
    return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(e.title + '_성적.csv')}"})


@router.post("/{eid}/delete")
def remove(eid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    e = _own(db, eid, teacher)
    db.execute(delete(ExamResponse).where(ExamResponse.exam_id == e.id))
    db.execute(delete(ExamClass).where(ExamClass.exam_id == e.id))
    title = e.title
    db.delete(e)
    db.commit()
    flash(request, f"'{title}' 시험과 학생 답안을 지웠어요.")
    return _go("/reports")
