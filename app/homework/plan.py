"""단어 숙제 자동 일정 (HomeworkPlan).

회차 r(0부터): 새 Day = units[start+r], 복습 = units[start+r-gap] (gap 회차 전에 배운 Day).
새 Day 가 다 끝나도 복습이 남은 회차(gap 개)까지 냄.
날짜: 이미 낸 마지막 회차 다음 날부터 고른 요일마다 하나씩. 정한 시각이 지나면 낸다.
서버가 자고 있었어도(무료 요금제) 깨어나는 순간 지난 회차를 따라잡아 냄 (catch_up).
"""
import logging
import threading
from datetime import date, datetime, timedelta

from sqlalchemy import select

from ..care import KST
from . import spec

log = logging.getLogger(__name__)
WEEKDAYS = "월화수목금토일"
_lock = threading.Lock()


def units_of(wl) -> list[str]:
    from ..vocab.spec import sources
    return [s for s in sources(wl.words) if s]


def rounds(plan, units: list[str]) -> list[dict]:
    seq = units[plan.start_unit:]
    g = max(0, plan.gap or 0)
    out = []
    for r in range(len(seq) + (g if seq else 0)):
        new = seq[r] if r < len(seq) else None
        rev = seq[r - g] if g and 0 <= r - g < len(seq) else None
        if new or rev:
            out.append({"r": r, "new": new, "review": rev})
    return out


def _d(s: str) -> date:
    return date.fromisoformat(s)


def schedule(plan, n: int) -> list[str]:
    """회차 0..n-1 의 날짜. 낸 회차는 그때 날짜, 나머지는 마지막으로 낸 날 다음부터 고른 요일마다."""
    made = plan.made or {}
    days = sorted({int(x) for x in plan.weekdays or []} & set(range(7)))
    out: list[str] = []
    cur = _d(plan.start_date) - timedelta(days=1)
    resume = (plan.settings or {}).get("resume")  # 멈췄다 다시 시작한 날: 그 전 날짜로는 안 냄
    floor = _d(resume) - timedelta(days=1) if resume else cur
    for r in range(n):
        m = made.get(str(r))
        if m:
            out.append(m["date"])
            cur = max(cur, _d(m["date"]))
            continue
        if not days:
            break
        d = max(cur, floor) + timedelta(days=1)
        while d.weekday() not in days:
            d += timedelta(days=1)
        out.append(d.isoformat())
        cur = d
    return out


def plan_table(plan, wl) -> list[dict]:
    rs = rounds(plan, units_of(wl)) if wl else []
    ds = schedule(plan, len(rs))
    for x, d in zip(rs, ds):
        x["date"] = d
        x["wd"] = WEEKDAYS[_d(d).weekday()]
        x["made"] = (plan.made or {}).get(str(x["r"]))
    return rs[:len(ds)]


def _due(ds: list[str], i: int, rule: str) -> str:
    d = _d(ds[i])
    if rule == "same":
        return d.isoformat()
    if i + 1 < len(ds):
        nxt = _d(ds[i + 1]) - timedelta(days=1)
        return max(d, nxt).isoformat()
    return (d + timedelta(days=2)).isoformat()


def make_round(db, plan, wl, x: dict, due: str):
    from ..models import Homework, HomeworkClass
    st = plan.settings or {}
    srcs = [u for u in (x["new"], x["review"]) if u]
    t = spec.task_vocab(wl, st.get("mode", "mc_en"), 9999, st.get("pass", 80), st.get("stages", "all"), srcs)
    label = " + ".join(f"{x['new']}" if u == x["new"] else f"{u} 복습" for u in srcs)
    t["title"] = f"단어 · {wl.title} ({label})"
    h = Homework(academy_id=plan.academy_id, teacher_id=plan.teacher_id, title=f"{plan.title} {x['r'] + 1}회 · {label}"[:120],
                 due=due, tasks=[t], options={"reveal": True, "plan_id": plan.id, "round": x["r"] + 1})
    db.add(h)
    db.flush()
    from ..models import ClassGroup
    ok = set(db.execute(select(ClassGroup.id).where(ClassGroup.academy_id == plan.academy_id,
                                                    ClassGroup.id.in_([int(c) for c in plan.class_ids or []] or [0]))).scalars())
    for cid in ok:
        db.add(HomeworkClass(homework_id=h.id, class_id=cid))
    return h


def catch_up(now: datetime | None = None, academy_id: int | None = None) -> list[int]:
    """시각이 지난 회차 숙제를 냄. 새로 낸 숙제 id 목록 (가장 최근 회차만 알림)."""
    from ..db import session_scope
    from ..models import HomeworkPlan, WordList
    now = now or datetime.now(KST)
    notify: list[int] = []
    if not _lock.acquire(blocking=False):
        return notify
    try:
        with session_scope() as db:
            q = select(HomeworkPlan).where(HomeworkPlan.active.is_(True)).with_for_update(skip_locked=True)
            if academy_id:
                q = q.where(HomeworkPlan.academy_id == academy_id)
            for plan in db.execute(q).scalars():
                wl = db.get(WordList, plan.list_id)
                if wl is None:
                    plan.active = False
                    continue
                rs = rounds(plan, units_of(wl))
                ds = schedule(plan, len(rs))
                made = dict(plan.made or {})
                last = None
                for i, x in enumerate(rs[:len(ds)]):
                    if str(x["r"]) in made:
                        continue
                    release = datetime.combine(_d(ds[i]), datetime.min.time(), KST).replace(hour=int(plan.hour or 0))
                    if now < release:
                        break
                    h = make_round(db, plan, wl, x, _due(ds, i, (plan.settings or {}).get("due", "next")))
                    made[str(x["r"])] = {"hid": h.id, "date": ds[i]}
                    last = (h.id, ds[i])
                plan.made = made
                if rs and all(str(x["r"]) in made for x in rs):
                    plan.active = False  # 다 냈음
                if last and last[1] == now.date().isoformat():
                    notify.append(last[0])
    except Exception:  # noqa: BLE001
        log.exception("homework plan catch_up")
    finally:
        _lock.release()
    if notify:
        from .. import push
        for hid in notify:
            push.in_background(push.homework_new, hid)
    return notify
