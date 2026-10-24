"""학생 화면: 로그인 없이 접속 코드로 들어옵니다.

지금 있는 것: 내 단어장(반에 내준 단어장) → 단어 카드 → 퀴즈(객관식·쓰기) → 결과·틀린 단어.
              시험 답 제출(선생님이 '학생 제출'을 연 시험) → 채점 결과(선생님이 보여 주기로 한 경우).
퀴즈 정답은 화면에 보내지 않고, 제출하면 서버가 채점합니다.
"""
import secrets
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..homework import spec as hspec
from ..online import build as obuild
from ..models import (Academy, Exam, ExamClass, ExamResponse, Homework, HomeworkClass, HomeworkResult, QuizResult, Student, WordList,
                      WordListClass)
from ..reports import data as rdata, spec as rspec
from ..vocab import spec
from ..web import render

router = APIRouter(prefix="/s")
GRACE = 60  # 제한 시간이 끝난 뒤 네트워크 지연을 봐주는 초


def _student(db: Session, code: str) -> Student | None:
    return db.execute(
        select(Student)
        .options(selectinload(Student.school), selectinload(Student.class_group))
        .where(Student.access_code == code.upper(), Student.active.is_(True))
    ).scalar_one_or_none()


def _lists_for(db: Session, s: Student) -> list[WordList]:
    if not s.class_id:
        return []
    return list(db.execute(
        select(WordList).join(WordListClass, WordListClass.list_id == WordList.id)
        .where(WordListClass.class_id == s.class_id, WordList.status == "done").order_by(WordList.id.desc())
    ).scalars())


def _list_for(db: Session, s: Student, lid: int) -> WordList:
    wl = next((w for w in _lists_for(db, s) if w.id == lid), None)
    if wl is None:
        raise HTTPException(404, "이 단어장을 볼 수 없어요. 선생님께 확인해 주세요.")
    return wl


def _exams_for(db: Session, s: Student) -> list[Exam]:
    if not s.class_id:
        return []
    exams = db.execute(select(Exam).join(ExamClass, ExamClass.exam_id == Exam.id).where(ExamClass.class_id == s.class_id).order_by(Exam.id.desc())).scalars()
    return [e for e in exams if (e.options or {}).get("open")]


def _exam_for(db: Session, s: Student, eid: int) -> Exam:
    e = next((x for x in _exams_for(db, s) if x.id == eid), None)
    if e is None:
        raise HTTPException(404, "이 시험은 지금 답을 낼 수 없어요. 선생님께 확인해 주세요.")
    return e


def _hws_for(db: Session, s: Student) -> list[Homework]:
    if not s.class_id:
        return []
    return list(db.execute(select(Homework).join(HomeworkClass, HomeworkClass.homework_id == Homework.id)
                           .where(HomeworkClass.class_id == s.class_id, Homework.active.is_(True)).order_by(Homework.id.desc()).limit(30)).scalars())


def _hw_for(db: Session, s: Student, hid: int) -> Homework:
    h = next((x for x in _hws_for(db, s) if x.id == hid), None)
    if h is None:
        raise HTTPException(404, "이 숙제는 지금 볼 수 없어요. 선생님께 확인해 주세요.")
    return h


def _hw_results(db: Session, s: Student, h: Homework) -> dict[int, HomeworkResult]:
    return {r.task: r for r in db.execute(select(HomeworkResult).where(HomeworkResult.homework_id == h.id, HomeworkResult.student_id == s.id)).scalars()}


def _record(db: Session, s: Student, h: Homework, i: int, correct: int, total: int, wrong: list) -> HomeworkResult:
    t = h.tasks[i]
    pct, ok = hspec.pass_done(t, correct, total)
    r = _hw_results(db, s, h).get(i)
    if r is None:
        r = HomeworkResult(academy_id=h.academy_id, homework_id=h.id, student_id=s.id, task=i, attempts=0, best=0)
        db.add(r)
    r.attempts = (r.attempts or 0) + 1
    r.correct, r.total, r.detail = correct, total, wrong
    if pct >= (r.best or 0):
        r.best = pct
    if ok and not r.done:
        r.done = True
        r.late = hspec.is_late(h.due)
    db.commit()
    return r


@router.get("")
def enter(request: Request, error: str = ""):
    return render(request, "student/enter.html", {"error": error})


@router.post("")
def enter_post(code: str = Form(...)):
    return RedirectResponse(f"/s/{code.strip().upper()}", status_code=303)


@router.get("/{code}")
def student_home(code: str, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        return render(request, "student/enter.html", {"error": "접속 코드를 찾을 수 없어요. 선생님께 코드를 다시 확인해 주세요.", "code": code}, 404)
    lists = _lists_for(db, s)
    best: dict[int, QuizResult] = {}
    for r in db.execute(select(QuizResult).where(QuizResult.student_id == s.id)).scalars():
        if r.list_id not in best or r.correct / max(1, r.total) > best[r.list_id].correct / max(1, best[r.list_id].total):
            best[r.list_id] = r
    hws = _hws_for(db, s)
    hw_prog = {}
    for h in hws:
        res = _hw_results(db, s, h)
        hw_prog[h.id] = sum(1 for i in range(len(h.tasks)) if res.get(i) and res[i].done)
    exams = _exams_for(db, s)
    done, going = set(), set()
    for r in db.execute(select(ExamResponse).where(ExamResponse.student_id == s.id)).scalars():
        m = r.meta or {}
        (going if m.get("started_at") and not m.get("submitted_at") else done).add(r.exam_id)
    return render(request, "student/home.html", {"student": s, "academy": db.get(Academy, s.academy_id), "lists": lists, "best": best,
                                                 "exams": exams, "done": done, "going": going, "online": {e.id for e in exams if obuild.is_online(e)}, "hws": hws, "hw_prog": hw_prog,
                                                 "today": hspec.today(), "code": s.access_code})


@router.get("/{code}/vocab/{lid}")
def vocab(code: str, lid: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    wl = _list_for(db, s, lid)
    history = list(db.execute(select(QuizResult).where(QuizResult.student_id == s.id, QuizResult.list_id == lid).order_by(QuizResult.id.desc()).limit(10)).scalars())
    return render(request, "student/vocab.html", {"student": s, "academy": db.get(Academy, s.academy_id), "wl": wl, "modes": spec.QUIZ_MODES, "history": history, "code": s.access_code})


@router.get("/{code}/vocab/{lid}/quiz")
def quiz(code: str, lid: int, request: Request, mode: str = "mc_en", count: int = 20, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    wl = _list_for(db, s, lid)
    mode = mode if mode in spec.QUIZ_MODES else "mc_en"
    count = max(5, min(100, count))
    seed = secrets.randbits(31)
    qs = spec.build_quiz(wl.words, mode, count, seed)
    if mode.startswith("mc") and len([w for w in wl.words if w.get("meaning")]) < 4:
        mode = "write_ko" if mode == "mc_en" else "write_en"
        qs = spec.build_quiz(wl.words, mode, count, seed)
    public = [{"prompt": q["prompt"], "choices": q.get("choices")} for q in qs]
    return render(request, "student/quiz.html", {"student": s, "academy": db.get(Academy, s.academy_id), "wl": wl, "mode": mode, "modes": spec.QUIZ_MODES,
                                                 "qs": public, "seed": seed, "count": count, "code": s.access_code})


@router.post("/{code}/vocab/{lid}/quiz")
async def submit(code: str, lid: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    wl = _list_for(db, s, lid)
    f = await request.form()
    mode = str(f.get("mode"))
    if mode not in spec.QUIZ_MODES:
        raise HTTPException(400)
    try:
        seed, count = int(f.get("seed")), int(f.get("count"))
    except (TypeError, ValueError):
        raise HTTPException(400)
    qs = spec.build_quiz(wl.words, mode, count, seed)  # 같은 seed로 같은 문제를 다시 만들어 채점
    wrong, correct, rows = [], 0, []
    for i, q in enumerate(qs):
        given = str(f.get(f"a{i}", "")).strip()
        ok = spec.grade(wl.words, mode, q["idx"], given)
        correct += ok
        w = wl.words[q["idx"]]
        rows.append({"prompt": q["prompt"], "answer": q["answer"], "given": given, "ok": ok})
        if not ok:
            wrong.append({"word": w["word"], "answer": q["answer"], "given": given})
    r = QuizResult(academy_id=s.academy_id, student_id=s.id, list_id=wl.id, mode=mode, total=len(qs), correct=correct, wrong=wrong)
    db.add(r)
    db.commit()
    return render(request, "student/result.html", {"student": s, "academy": db.get(Academy, s.academy_id), "wl": wl, "r": r, "rows": rows,
                                                   "mode": mode, "modes": spec.QUIZ_MODES, "count": count, "code": s.access_code})


def _response(db: Session, e: Exam, s: Student) -> ExamResponse | None:
    return db.execute(select(ExamResponse).where(ExamResponse.exam_id == e.id, ExamResponse.student_id == s.id)).scalar_one_or_none()


def _now() -> float:
    return time.time()


def _deadline(e: Exam, r: ExamResponse | None) -> float | None:
    """시작 시각 + 제한 시간(분). 제한 시간이 없으면 None."""
    minutes = int((e.options or {}).get("minutes") or 0)
    started = (r.meta or {}).get("started_at") if r else None
    return started + minutes * 60 if minutes and started else None


def _parse_answers(items: list[dict], get) -> tuple[dict, dict]:
    answers, texts = {}, {}
    for it in items:
        k = str(it["n"])
        if it["kind"] == "mc":
            v = str(get(f"a{k}") or "").strip()
            n_choices = len(((it.get("content") or {}).get("choices")) or []) or 5
            if v in rspec.CHOICES[:max(2, n_choices)]:
                answers[k] = v
        else:
            t = str(get(f"t{k}") or "").strip()[:1000]
            if t:
                texts[k] = t
    return answers, texts


def _finish(db: Session, e: Exam, r: ExamResponse, answers: dict, texts: dict, late: bool = False):
    """온라인 시험 제출: 임시 저장을 정식 답안으로 옮기고 제출 시각을 남김 (이후엔 다시 못 냄)."""
    r.answers, r.sa_text = answers, texts
    meta = dict(r.meta or {})
    meta.pop("draft", None)
    meta.update(submitted_at=_now(), late=late)
    r.meta = meta
    db.commit()


@router.get("/{code}/exam/{eid}")
def exam(code: str, eid: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    e = _exam_for(db, s, eid)
    r = _response(db, e, s)
    base = {"student": s, "academy": db.get(Academy, s.academy_id), "e": e, "r": r, "circ": rspec.CIRCLED, "fmt": rspec.fmt, "code": s.access_code}
    items = rspec.clean_items(e.items or [])
    if obuild.is_online(e):
        meta = (r.meta or {}) if r else {}
        if meta.get("submitted_at") or (r and (r.answers or r.sa_scores) and not meta.get("started_at")):
            return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}/result", status_code=303)
        minutes = int((e.options or {}).get("minutes") or 0)
        if not meta.get("started_at"):
            return render(request, "student/exam_intro.html", {**base, "n": len(items), "n_sa": sum(it["kind"] == "sa" for it in items), "minutes": minutes})
        dl = _deadline(e, r)
        if dl is not None and _now() > dl + GRACE:  # 시간이 지나 창을 닫았던 경우: 임시 저장한 답으로 제출 처리
            d = meta.get("draft") or {}
            _finish(db, e, r, d.get("a") or {}, d.get("t") or {}, late=True)
            return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}/result", status_code=303)
        draft = meta.get("draft") or {}
        return render(request, "student/exam_online.html", {**base, "items": obuild.public_items(items), "draft": {"a": draft.get("a") or {}, "t": draft.get("t") or {}},
                                                            "left": None if dl is None else max(0, int(dl - _now())), "minutes": minutes, "preview": False})
    items = [{k: v for k, v in it.items() if k not in ("answer", "content")} for it in items]  # 정답은 보내지 않음
    return render(request, "student/exam.html", {**base, "items": items})


@router.post("/{code}/exam/{eid}/start")
def exam_start(code: str, eid: int, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    e = _exam_for(db, s, eid)
    r = _response(db, e, s)
    if r is None:
        r = ExamResponse(academy_id=e.academy_id, exam_id=e.id, student_id=s.id, by="student", answers={}, sa_text={}, sa_scores={}, meta={})
        db.add(r)
    if not (r.meta or {}).get("started_at"):
        r.meta = {**(r.meta or {}), "started_at": _now()}
        db.commit()
    return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}", status_code=303)


@router.post("/{code}/exam/{eid}/save")
async def exam_save(code: str, eid: int, request: Request, db: Session = Depends(get_db)):
    """푸는 동안 자동 저장 (JSON {a: {"1": "3"}, t: {"21": "..."}}). 휴대폰이 꺼져도 이어서 풀 수 있게."""
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    e = _exam_for(db, s, eid)
    r = _response(db, e, s)
    meta = (r.meta or {}) if r else {}
    if r is None or not meta.get("started_at") or meta.get("submitted_at"):
        return JSONResponse({"ok": False, "reason": "closed"}, status_code=409)
    dl = _deadline(e, r)
    if dl is not None and _now() > dl + GRACE:
        return JSONResponse({"ok": False, "reason": "time"}, status_code=409)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400)
    a, t = (body.get("a") or {}), (body.get("t") or {})
    if not isinstance(a, dict) or not isinstance(t, dict):
        raise HTTPException(400)
    answers, texts = _parse_answers(rspec.clean_items(e.items or []), lambda k: a.get(k[1:]) if k[0] == "a" else t.get(k[1:]))
    r.meta = {**meta, "draft": {"a": answers, "t": texts}, "saved_at": _now()}
    db.commit()
    return {"ok": True, "left": None if dl is None else max(0, int(dl - _now()))}


@router.post("/{code}/exam/{eid}")
async def exam_submit(code: str, eid: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    e = _exam_for(db, s, eid)
    f = await request.form()
    items = rspec.clean_items(e.items or [])
    answers, texts = _parse_answers(items, f.get)
    r = _response(db, e, s)
    if obuild.is_online(e):
        meta = (r.meta or {}) if r else {}
        if r is None or not meta.get("started_at"):
            return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}", status_code=303)
        if not meta.get("submitted_at"):
            dl = _deadline(e, r)
            if dl is not None and _now() > dl + GRACE:  # 시간 지나서 낸 답은 받지 않고, 시간 안에 저장된 답으로
                d = meta.get("draft") or {}
                answers, texts = d.get("a") or {}, d.get("t") or {}
            _finish(db, e, r, answers, texts, late=bool(dl is not None and _now() > dl + 5))
        return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}/result", status_code=303)
    if r is None:
        r = ExamResponse(academy_id=e.academy_id, exam_id=e.id, student_id=s.id, by="student")
        db.add(r)
    r.answers, r.sa_text = answers, texts  # 선생님이 넣은 서술형 점수·코멘트는 그대로
    db.commit()
    return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}/result", status_code=303)


@router.get("/{code}/exam/{eid}/result")
def exam_result(code: str, eid: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    e = _exam_for(db, s, eid)
    r = db.execute(select(ExamResponse).where(ExamResponse.exam_id == e.id, ExamResponse.student_id == s.id)).scalar_one_or_none()
    online = obuild.is_online(e)
    if r is None or (online and not (r.meta or {}).get("submitted_at") and (r.meta or {}).get("started_at")):
        return RedirectResponse(f"/s/{s.access_code}/exam/{e.id}", status_code=303)
    view = None
    if (e.options or {}).get("reveal"):
        S, _, _ = rdata.summary(db, e)
        view = rspec.student_view(S, s.id)
    return render(request, "student/exam_result.html", {"student": s, "academy": db.get(Academy, s.academy_id), "e": e, "r": r, "view": view,
                                                        "n_mc": sum(1 for it in (e.items or []) if it.get("kind") != "sa"), "online": online,
                                                        "circ": rspec.CIRCLED, "fmt": rspec.fmt, "code": s.access_code})


# ---------------- 숙제 ----------------

def _ctx(db, s, **kw):
    return {"student": s, "academy": db.get(Academy, s.academy_id), "code": s.access_code, **kw}


@router.get("/{code}/hw/{hid}")
def hw_home(code: str, hid: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        return render(request, "student/enter.html", {"error": "접속 코드를 찾을 수 없어요. 선생님께 코드를 다시 확인해 주세요.", "code": code}, 404)
    h = _hw_for(db, s, hid)
    res = _hw_results(db, s, h)
    rows = [{"i": i, "t": t, "r": res.get(i), "st": hspec.status(t, res.get(i)), "sum": hspec.summary_text(t)} for i, t in enumerate(h.tasks)]
    return render(request, "student/hw.html", _ctx(db, s, h=h, rows=rows, kinds=hspec.KINDS, late=hspec.is_late(h.due)))


@router.get("/{code}/hw/{hid}/{i}")
def hw_task(code: str, hid: int, i: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    h = _hw_for(db, s, hid)
    if not 0 <= i < len(h.tasks):
        raise HTTPException(404)
    t = h.tasks[i]
    if t["kind"] == "vocab":
        wl = db.get(WordList, t["list_id"])
        if wl is None:
            raise HTTPException(404, "단어장이 지워졌어요. 선생님께 알려 주세요.")
        seed = secrets.randbits(31)
        qs = spec.build_quiz(wl.words, t["mode"], t["count"], seed)
        public = [{"prompt": q["prompt"], "choices": q.get("choices")} for q in qs]
        return render(request, "student/quiz.html", _ctx(db, s, wl=wl, mode=t["mode"], modes=spec.QUIZ_MODES, qs=public, seed=seed, count=t["count"],
                                                         hw=h, action=f"/s/{s.access_code}/hw/{h.id}/{i}"))
    if t["kind"] == "ox":
        items = [{"q": x["q"], "p": x["p"]} for x in t["items"]]  # 정답은 보내지 않음
        return render(request, "student/hw_ox.html", _ctx(db, s, h=h, i=i, t=t, items=items))
    if t["kind"] == "omr":
        nums = [it["n"] for it in t["items"]]
        return render(request, "student/hw_omr.html", _ctx(db, s, h=h, i=i, t=t, nums=nums, circ=rspec.CIRCLED))
    return RedirectResponse(f"/s/{s.access_code}/hw/{h.id}", status_code=303)


@router.post("/{code}/hw/{hid}/{i}")
async def hw_submit(code: str, hid: int, i: int, request: Request, db: Session = Depends(get_db)):
    s = _student(db, code)
    if s is None:
        raise HTTPException(404)
    h = _hw_for(db, s, hid)
    if not 0 <= i < len(h.tasks):
        raise HTTPException(404)
    t = h.tasks[i]
    f = await request.form()
    reveal = (h.options or {}).get("reveal")
    if t["kind"] == "check":
        r = _hw_results(db, s, h).get(i)
        undo = f.get("undo") == "1"
        if r is None:
            r = HomeworkResult(academy_id=h.academy_id, homework_id=h.id, student_id=s.id, task=i, attempts=0, best=0)
            db.add(r)
        r.done, r.attempts, r.best = (not undo), max(1, r.attempts or 0) if not undo else 0, 0 if undo else 100
        r.late = (not undo) and hspec.is_late(h.due)
        db.commit()
        return RedirectResponse(f"/s/{s.access_code}/hw/{h.id}", status_code=303)
    if t["kind"] == "vocab":
        wl = db.get(WordList, t["list_id"])
        if wl is None:
            raise HTTPException(404)
        try:
            seed, count = int(f.get("seed")), int(f.get("count"))
        except (TypeError, ValueError):
            raise HTTPException(400)
        mode = t["mode"]
        qs = spec.build_quiz(wl.words, mode, count, seed)
        wrong, correct, rows = [], 0, []
        for k, q in enumerate(qs):
            given = str(f.get(f"a{k}", "")).strip()
            ok = spec.grade(wl.words, mode, q["idx"], given)
            correct += ok
            rows.append({"prompt": q["prompt"], "answer": q["answer"], "given": given, "ok": ok})
            if not ok:
                wrong.append({"word": wl.words[q["idx"]]["word"], "answer": q["answer"], "given": given})
        qr = QuizResult(academy_id=s.academy_id, student_id=s.id, list_id=wl.id, mode=mode, total=len(qs), correct=correct, wrong=wrong)
        db.add(qr)
        r = _record(db, s, h, i, correct, len(qs), [{"n": w["word"]} for w in wrong])
        return render(request, "student/result.html", _ctx(db, s, wl=wl, r=qr, rows=rows, mode=mode, modes=spec.QUIZ_MODES, count=count,
                                                           hw=h, hw_task=i, hw_done=r.done, hw_pass=t["pass"]))
    if t["kind"] == "ox":
        correct, wrong = hspec.grade_ox(t, f)
        total = len(t["items"])
    else:
        correct, wrong = hspec.grade_omr(t, f)
        total = len(t["items"])
    r = _record(db, s, h, i, correct, total, wrong)
    for w in wrong:
        if "a" in w and t["kind"] == "omr":
            w["a_label"], w["g_label"] = hspec.answer_label(w["a"]), hspec.answer_label(w["given"]) if w["given"] else "안 씀"
    return render(request, "student/hw_result.html", _ctx(db, s, h=h, i=i, t=t, r=r, correct=correct, total=total, wrong=wrong,
                                                          reveal=reveal, pct=round(100 * correct / total) if total else 0))
