import copy
"""숙제 (선생님 화면): 숙제 내기 → 반 학생 진행 표 → 카톡 안내·독촉 문구 복사."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, delete, select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..homework import spec
from ..models import (Academy, ClassGroup, GrammarSet, Homework, HomeworkClass, HomeworkResult, LevelTest, Student, Teacher, VariantSet, WordList,
                      Workbook)
from ..vocab.spec import QUIZ_MODES
from ..web import current_teacher, flash, render

router = APIRouter(prefix="/homework")


def _go(url: str):
    return RedirectResponse(url, status_code=303)


def _own(db: Session, hid: int, teacher: Teacher) -> Homework:
    h = db.get(Homework, hid)
    if h is None or h.academy_id != teacher.academy_id:
        raise HTTPException(404, "숙제를 찾을 수 없습니다.")
    return h


def _classes(db: Session, teacher: Teacher) -> list[ClassGroup]:
    return list(db.execute(select(ClassGroup).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.active.is_(True)).order_by(ClassGroup.name)).scalars())


def _sources(db: Session, aid: int) -> dict:
    """숙제로 낼 수 있는 자료 목록 (최근 것부터)."""
    def q(M, *conds):
        return list(db.execute(select(M).where(M.academy_id == aid, *conds).order_by(M.id.desc()).limit(60)).scalars())
    wbs = [w for w in q(Workbook) if any(p.get("status") == "done" for p in (w.passages or []))]
    vss = [v for v in q(VariantSet, VariantSet.status == "done") if spec.variant_key(v)]
    gss = [g for g in q(GrammarSet, GrammarSet.status == "done") if spec.grammar_key(g)]
    lts = [t for t in db.execute(select(LevelTest).where(LevelTest.academy_id == aid).order_by(LevelTest.sort, LevelTest.id)).scalars()
           if (t.spec or {}).get("sections")]
    from ..vocab.spec import sources
    vls = q(WordList, WordList.status == "done")
    for w in vls:
        w.srcs = sources(w.words)
    return {"vocab": vls, "workbook": wbs, "variant": vss, "grammar": gss, "leveltest": lts}


def _set_classes(db: Session, h: Homework, teacher: Teacher, raw: list) -> int:
    ids = {int(x) for x in raw if str(x).isdigit()}
    valid = set(db.execute(select(ClassGroup.id).where(ClassGroup.academy_id == teacher.academy_id, ClassGroup.id.in_(ids))).scalars()) if ids else set()
    db.execute(delete(HomeworkClass).where(HomeworkClass.homework_id == h.id))
    for cid in valid:
        db.add(HomeworkClass(homework_id=h.id, class_id=cid))
    return len(valid)


def roster(db: Session, academy_id: int) -> dict:
    """반별 학생 {반 id: [{id, name}]} — 숙제 낼 때 반 안에서 학생 고르기용."""
    out: dict = {}
    for st in db.execute(select(Student).where(Student.academy_id == academy_id, Student.active.is_(True), Student.class_id.isnot(None))
                         .order_by(Student.name)).scalars():
        out.setdefault(str(st.class_id), []).append({"id": st.id, "name": st.name})
    return out


def _set_students(db: Session, h: Homework, f) -> None:
    """'학생 고르기'에서 일부만 골랐으면 options.students, 반 전체면 비움."""
    opts = {k: v for k, v in (h.options or {}).items() if k != "students"}
    if f.get("pick_students") == "1":
        cids = [c for (c,) in db.execute(select(HomeworkClass.class_id).where(HomeworkClass.homework_id == h.id))]
        everyone = set(db.execute(select(Student.id).where(Student.class_id.in_(cids or [0]), Student.active.is_(True))).scalars())
        chosen = {int(x) for x in f.getlist("student_id") if str(x).isdigit()} & everyone
        if chosen and chosen != everyone:
            opts["students"] = sorted(chosen)
    h.options = opts


def _json_list(v: str) -> list[str]:
    import json
    try:
        x = json.loads(v or "[]")
    except ValueError:
        return []
    return [str(i) for i in x] if isinstance(x, list) else []


def _date(v) -> str:
    s = str(v or "").strip()
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else ""


def _build_tasks(db: Session, aid: int, f) -> tuple[list[dict], list[str]]:
    srcs, modes, counts, passes, texts = (f.getlist(k) for k in ("t_src", "t_mode", "t_count", "t_pass", "t_text"))
    tasks, errors = [], []
    for i, src in enumerate(srcs):
        src = str(src)
        if not src:
            continue
        g = lambda lst, d="": str(lst[i]) if i < len(lst) else d  # noqa: E731
        kind, _, ref = src.partition(":")
        n = len(tasks) + 1
        if kind == "check":
            if not g(texts).strip():
                errors.append(f"할 일 {n}: 무엇을 할지 적어 주세요.")
                continue
            tasks.append(spec.task_check(g(texts)))
        elif kind == "key":
            items = spec.key_items(g(texts))
            if not items:
                errors.append(f"할 일 {n}: 정답을 넣어 주세요. (예: 35214 …)")
                continue
            title = re.sub(r"\s+", " ", f.getlist("t_title")[i] if i < len(f.getlist("t_title")) else "").strip()[:80] or "답 입력"
            tasks.append(spec.task_omr(title, "key", items, g(passes)))
        elif kind == "leveltest" and ref.isdigit():
            obj = db.get(LevelTest, int(ref))
            if obj is None or obj.academy_id != aid:
                errors.append(f"할 일 {n}: 레벨테스트를 찾을 수 없어요.")
                continue
            tasks.append(spec.task_leveltest(obj))
        elif kind in ("vocab", "workbook", "variant", "grammar") and ref.isdigit():
            M = {"vocab": WordList, "workbook": Workbook, "variant": VariantSet, "grammar": GrammarSet}[kind]
            obj = db.get(M, int(ref))
            if obj is None or obj.academy_id != aid:
                errors.append(f"할 일 {n}: 자료를 찾을 수 없어요.")
                continue
            if kind == "vocab":
                t = spec.task_vocab(obj, g(modes, "mc_en"), g(counts, "20"), g(passes, "80"), g(f.getlist("t_stages"), "all"), _json_list(g(f.getlist("t_vsrcs"))))
                if not t["count"]:
                    errors.append(f"할 일 {n}: 뜻이 채워진 단어가 없어요.")
                    continue
            elif kind == "workbook":
                t = spec.task_ox(obj, g(modes, "all") if g(modes) in spec.OX_LEVELS else "all", g(passes, "0"))
                if not t["items"]:
                    errors.append(f"할 일 {n}: 이 워크북에 OX 문항이 없어요.")
                    continue
            elif kind == "variant":
                t = spec.task_omr(f"변형문제 · {obj.title}", f"variant:{obj.id}", spec.variant_key(obj), g(passes, "0"))
                if f.get("show_q") == "1":
                    spec.with_questions(t, spec.variant_questions(obj))
            else:
                t = spec.task_omr(f"어법 문제 · {obj.title}", f"grammar:{obj.id}", spec.grammar_key(obj), g(passes, "0"))
                if f.get("show_q") == "1":
                    spec.with_questions(t, spec.grammar_questions(obj))
            tasks.append(t)
        else:
            errors.append(f"할 일 {n}: 종류를 다시 골라 주세요.")
    if not tasks and not errors:
        errors.append("할 일을 하나 이상 넣어 주세요.")
    return tasks[: spec.MAX_TASKS], errors


def _students(db: Session, h: Homework, class_id: int | None = None) -> list[Student]:
    cids = [c.id for c in h.classes]
    if class_id:
        cids = [c for c in cids if c == class_id]
    if not cids:
        return []
    return [s for s in db.execute(select(Student).options(selectinload(Student.class_group))
                                  .where(Student.class_id.in_(cids), Student.active.is_(True)).order_by(Student.name)).scalars()
            if spec.for_student(h, s.id)]


@router.get("")
def index(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    from ..homework.plan import catch_up
    catch_up(academy_id=teacher.academy_id)
    from ..models import HomeworkPlan
    plans = list(db.execute(select(HomeworkPlan).options(selectinload(HomeworkPlan.word_list)).where(HomeworkPlan.academy_id == teacher.academy_id)
                            .order_by(HomeworkPlan.active.desc(), HomeworkPlan.id.desc())).scalars())
    hws = list(db.execute(select(Homework).options(selectinload(Homework.classes), selectinload(Homework.teacher))
                          .where(Homework.academy_id == teacher.academy_id).order_by(Homework.id.desc()).limit(200)).scalars())
    # 완료율: (학생 × 할 일) 중 done
    rate = {}
    for h in hws:
        studs = _students(db, h)
        total = len(studs) * len(h.tasks or [])
        if total:
            done = len(list(db.execute(select(HomeworkResult.id).where(HomeworkResult.homework_id == h.id, HomeworkResult.done.is_(True),
                                                                       HomeworkResult.student_id.in_([s.id for s in studs]))).scalars()))
            rate[h.id] = (done, total, len(studs))
    from .reports import online_exams
    from ..models import QuizResult
    from sqlalchemy import func
    open_lists = [wl for wl in db.execute(select(WordList).options(selectinload(WordList.classes)).where(WordList.academy_id == teacher.academy_id)
                                          .order_by(WordList.id.desc()).limit(300)).scalars() if wl.classes]
    tried = dict(db.execute(select(QuizResult.list_id, func.count(func.distinct(QuizResult.student_id)))
                            .where(QuizResult.list_id.in_([w.id for w in open_lists] or [0])).group_by(QuizResult.list_id)).all())
    return render(request, "homework/index.html", {"online": online_exams(db, teacher.academy_id),
                                                   "open_lists": [{"wl": w, "n": tried.get(w.id, 0)} for w in open_lists], "teacher": teacher, "hws": hws, "plans": plans, "rate": rate, "today": spec.today(), "active": "care"})


@router.get("/new")
def new(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    return render(request, "homework/new.html", {"teacher": teacher, "form": {"reveal": True, "show_q": True}, "classes": _classes(db, teacher), "roster": roster(db, teacher.academy_id),
                                                 "src": _sources(db, teacher.academy_id), "modes": QUIZ_MODES, "stage_sets": spec.STAGE_SETS, "ox_levels": spec.OX_LEVELS,
                                                 "max": spec.MAX_TASKS, "active": "care"})


@router.post("")
async def create(request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    f = await request.form()
    title = str(f.get("title", "")).strip()[:120]
    tasks, errors = _build_tasks(db, teacher.academy_id, f)
    class_ids = [x for x in f.getlist("class_id") if str(x).isdigit()]
    if not title:
        errors.insert(0, "숙제 이름을 넣어 주세요.")
    if not class_ids:
        errors.append("숙제를 낼 반을 골라 주세요.")
    if errors:
        form = {"title": title, "due": f.get("due"), "memo": f.get("memo"), "reveal": f.get("reveal") == "1", "show_q": f.get("show_q") == "1", "class_ids": {int(x) for x in class_ids}}
        return render(request, "homework/new.html", {"teacher": teacher, "form": form, "errors": errors, "classes": _classes(db, teacher), "roster": roster(db, teacher.academy_id),
                                                     "src": _sources(db, teacher.academy_id), "modes": QUIZ_MODES, "stage_sets": spec.STAGE_SETS, "ox_levels": spec.OX_LEVELS,
                                                     "max": spec.MAX_TASKS, "active": "care"}, 400)
    h = Homework(academy_id=teacher.academy_id, teacher_id=teacher.id, title=title, due=_date(f.get("due")),
                 memo=str(f.get("memo", "")).strip()[:1000], tasks=tasks, options={"reveal": f.get("reveal") == "1"})
    db.add(h)
    db.flush()
    _set_classes(db, h, teacher, class_ids)
    db.flush()
    _set_students(db, h, f)
    db.commit()
    from .. import push
    push.in_background(push.homework_new, h.id)  # 알림 켠 학생 폰에 '새 숙제'
    flash(request, "숙제를 냈어요. 아래 '카톡 문구'를 복사해 단톡방에 올리면 학생이 링크로 바로 들어와요.")
    return _go(f"/homework/{h.id}")


@router.post("/{hid}/remind")
def remind(hid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """아직 다 안 한 학생 폰에 알림."""
    h = _own(db, hid, teacher)
    from .. import push
    from ..models import PushSub
    ids = push.not_done_ids(db, h)
    on = db.execute(select(func.count(func.distinct(PushSub.student_id))).where(PushSub.student_id.in_(ids or [0]))).scalar() or 0
    if not ids:
        flash(request, "모두 다 했어요. 알림 보낼 학생이 없어요.")
    elif not on:
        flash(request, f"안 한 학생 {len(ids)}명 중 알림을 켠 학생이 없어요. 학생 화면의 '🔔 숙제 알림 받기'를 켜 달라고 해 주세요.", "error")
    else:
        push.in_background(push.homework_remind, h.id)
        flash(request, f"안 한 학생 {len(ids)}명 중 알림을 켠 {on}명에게 알림을 보냈어요.")
    return _go(f"/homework/{hid}")


@router.get("/{hid}")
def detail(hid: int, request: Request, class_id: str = "", teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    h = _own(db, hid, teacher)
    cid = int(class_id) if class_id.isdigit() else None
    studs = _students(db, h, cid)
    res = {}
    for r in db.execute(select(HomeworkResult).where(HomeworkResult.homework_id == h.id)).scalars():
        res[(r.student_id, r.task)] = r
    rows, todo_people = [], []
    base = str(request.base_url).rstrip("/")
    academy = db.get(Academy, h.academy_id)
    for s in studs:
        cells = [(spec.status(t, res.get((s.id, i))), res.get((s.id, i))) for i, t in enumerate(h.tasks)]
        n_done = sum(1 for st, _ in cells if st == "done")
        rows.append({"s": s, "cells": cells, "done": n_done})
        if n_done < len(h.tasks):
            left = [h.tasks[i]["title"] for i, (st, _) in enumerate(cells) if st != "done"]
            todo_people.append({"s": s, "text": spec.remind_text(academy.name, h, s.name, f"{base}/s/{s.access_code}/hw/{h.id}", left)})
    per_task = []
    for i, t in enumerate(h.tasks):
        rs = [res[(s.id, i)] for s in studs if (s.id, i) in res and res[(s.id, i)].attempts]
        miss = {}
        for r in rs:
            for w in r.detail or []:
                key = w.get("n") or w.get("i")
                miss[key] = miss.get(key, 0) + 1
        per_task.append({"t": t, "done": sum(1 for r in rs if r.done), "tried": len(rs),
                         "avg": round(sum(r.best for r in rs) / len(rs)) if rs and t["kind"] != "check" else None,
                         "miss": sorted(miss.items(), key=lambda kv: -kv[1])[:6]})
    due = f" (마감 {h.due[5:].replace('-', '/')})" if h.due else ""
    notice = "\n".join([f"[{academy.name}] 숙제 안내", f"■ {h.title}{due}"] + [f" - {t['title']}" for t in h.tasks]
                       + ([h.memo] if h.memo else []) + [f"각자 접속 코드로 들어와서 풀어요: {base}/s"])
    from ..models import PushSub
    alert_on = set(db.execute(select(PushSub.student_id).where(PushSub.student_id.in_([r["s"].id for r in rows] or [0]))).scalars())
    return render(request, "homework/detail.html", {
        "alert_on": alert_on, "teacher": teacher, "h": h, "rows": rows, "per_task": per_task, "todo_people": todo_people, "notice": notice,
        "kinds": spec.KINDS, "summary": spec.summary_text, "classes": _classes(db, teacher), "assigned": {c.id for c in h.classes}, "roster": roster(db, teacher.academy_id), "picked": (h.options or {}).get("students") or [],
        "class_id": cid, "late": spec.is_late(h.due), "active": "care",
    })


@router.post("/{hid}/edit")
async def edit(hid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    h = _own(db, hid, teacher)
    f = await request.form()
    h.title = str(f.get("title", "")).strip()[:120] or h.title
    h.due = _date(f.get("due"))
    h.memo = str(f.get("memo", "")).strip()[:1000]
    h.active = f.get("active") == "1"
    h.options = {**(h.options or {}), "reveal": f.get("reveal") == "1"}
    if f.get("has_qtoggle") == "1":  # 변형·어법 숙제: 학생 화면에 문제 보여 주기 켜고 끄기
        show = f.get("show_q") == "1"
        tasks = copy.deepcopy(h.tasks or [])
        for t in tasks:
            kind, _, ref = str(t.get("source") or "").partition(":")
            if t.get("kind") != "omr" or kind not in ("variant", "grammar") or not ref.isdigit():
                continue
            for it in t.get("items") or []:
                it.pop("q", None)
            obj = db.get(VariantSet if kind == "variant" else GrammarSet, int(ref)) if show else None
            if obj is not None and obj.academy_id == h.academy_id:
                spec.with_questions(t, spec.variant_questions(obj) if kind == "variant" else spec.grammar_questions(obj))
        h.tasks = tasks
        flag_modified(h, "tasks")
    _set_classes(db, h, teacher, f.getlist("class_id"))
    db.flush()
    _set_students(db, h, f)
    db.commit()
    flash(request, "저장했어요." + ("" if h.active else " 닫은 숙제는 학생 화면에 보이지 않아요."))
    return _go(f"/homework/{hid}")


@router.post("/{hid}/check/{sid}/{task}")
def teacher_check(hid: int, sid: int, task: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    """선생님이 직접 완료/미완료로 바꾸기 (종이 숙제 확인 등)."""
    h = _own(db, hid, teacher)
    if not (0 <= task < len(h.tasks)) or sid not in {s.id for s in _students(db, h)}:
        raise HTTPException(404)
    r = db.execute(select(HomeworkResult).where(HomeworkResult.homework_id == h.id, HomeworkResult.student_id == sid,
                                                HomeworkResult.task == task)).scalar_one_or_none()
    if r is None:
        r = HomeworkResult(academy_id=h.academy_id, homework_id=h.id, student_id=sid, task=task, attempts=0)
        db.add(r)
    r.done = not r.done
    if r.done and not r.attempts:
        r.attempts = 1
        r.best = 100 if h.tasks[task]["kind"] == "check" else r.best
    db.commit()
    return _go(f"/homework/{hid}#grid")


@router.post("/{hid}/delete")
def remove(hid: int, request: Request, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    h = _own(db, hid, teacher)
    db.execute(delete(HomeworkResult).where(HomeworkResult.homework_id == h.id))
    db.execute(delete(HomeworkClass).where(HomeworkClass.homework_id == h.id))
    title = h.title
    db.delete(h)
    db.commit()
    flash(request, f"'{title}' 숙제와 학생 기록을 지웠어요.")
    return _go("/homework")
