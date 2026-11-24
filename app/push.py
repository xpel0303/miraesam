"""숙제 푸시 알림 (웹 푸시).

- VAPID 키는 처음 쓸 때 만들어 DB(app_secrets)에 저장 → 환경변수를 건드리지 않음.
- 학생이 '숙제 알림 받기'를 누르면 기기(push_subs)를 저장.
- 숙제를 내면 그 반 학생에게, 마감 전날 저녁(17시 이후)에는 아직 다 안 한 학생에게 알림.
  (무료 서버가 잠들어 있으면 전날 알림이 늦을 수 있어 숙제 화면에 '안 한 학생에게 알림' 버튼도 있음)
"""
import base64
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from .db import session_scope
from .models import AppSecret, Homework, HomeworkClass, HomeworkResult, PushSub, Student

log = logging.getLogger("push")
KST = ZoneInfo("Asia/Seoul")
_keys: dict | None = None
_lock = threading.Lock()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def keys() -> dict:
    """{'public': 앱에 주는 키(base64url), 'private_pem': 서버만 쓰는 키}"""
    global _keys
    if _keys:
        return _keys
    with _lock, session_scope() as db:
        row = db.get(AppSecret, "vapid")
        if row is None:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            k = ec.generate_private_key(ec.SECP256R1())
            pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
            pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            row = AppSecret(key="vapid", value=json.dumps({"public": _b64(pub), "private_pem": pem}))
            db.add(row)
        _keys = json.loads(row.value)
    return _keys


def save_sub(academy_id: int, student_id: int | None, sub: dict) -> bool:
    ep, k = str(sub.get("endpoint") or ""), sub.get("keys") or {}
    if not ep.startswith("https://") or not k.get("p256dh") or not k.get("auth"):
        return False
    with session_scope() as db:
        row = db.execute(select(PushSub).where(PushSub.endpoint == ep)).scalar_one_or_none()
        if row is None:
            row = PushSub(endpoint=ep)
            db.add(row)
        row.academy_id, row.student_id, row.p256dh, row.auth = academy_id, student_id, str(k["p256dh"])[:200], str(k["auth"])[:100]
    return True


def drop_sub(endpoint: str):
    with session_scope() as db:
        db.execute(delete(PushSub).where(PushSub.endpoint == endpoint))


def _send_all(subs: list[tuple[str, str, str]], payload: dict) -> int:
    from pywebpush import WebPushException, webpush
    from py_vapid import Vapid
    v = Vapid.from_pem(keys()["private_pem"].encode())
    sent, dead = 0, []
    data = json.dumps(payload, ensure_ascii=False)
    for ep, p256, auth in subs:
        try:
            webpush({"endpoint": ep, "keys": {"p256dh": p256, "auth": auth}}, data, vapid_private_key=v,
                    vapid_claims={"sub": "mailto:noreply@miraesam.onrender.com"}, ttl=60 * 60 * 24, timeout=10)
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):  # 앱을 지웠거나 알림을 끈 기기
                dead.append(ep)
            else:
                log.warning("push failed %s %s", code, str(e)[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("push error %s", str(e)[:200])
    if dead:
        with session_scope() as db:
            db.execute(delete(PushSub).where(PushSub.endpoint.in_(dead)))
    return sent


def notify_students(student_ids: list[int], title: str, body: str, url_for) -> int:
    """학생마다 다른 주소(url_for(student) → '/s/코드/...')로 알림. 보낸 기기 수."""
    if not student_ids:
        return 0
    with session_scope() as db:
        rows = db.execute(select(PushSub, Student).join(Student, Student.id == PushSub.student_id)
                          .where(PushSub.student_id.in_(student_ids))).all()
        by_url: dict[str, list] = {}
        for sub, st in rows:
            by_url.setdefault(url_for(st), []).append((sub.endpoint, sub.p256dh, sub.auth))
    n = 0
    for url, subs in by_url.items():
        n += _send_all(subs, {"title": title, "body": body, "url": url, "tag": url})
    return n


def in_background(fn, *args):
    threading.Thread(target=lambda: _safe(fn, *args), daemon=True).start()


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("push background failed")


# ---------------------------------------------------------------- 숙제 알림

def _hw_students(db, h: Homework) -> list[Student]:
    cids = [c for (c,) in db.execute(select(HomeworkClass.class_id).where(HomeworkClass.homework_id == h.id))]
    if not cids:
        return []
    return list(db.execute(select(Student).where(Student.class_id.in_(cids), Student.active.is_(True))).scalars())


def not_done_ids(db, h: Homework) -> list[int]:
    studs = _hw_students(db, h)
    done: dict[int, int] = {}
    for sid, in db.execute(select(HomeworkResult.student_id).where(HomeworkResult.homework_id == h.id, HomeworkResult.done.is_(True))):
        done[sid] = done.get(sid, 0) + 1
    return [s.id for s in studs if done.get(s.id, 0) < len(h.tasks or [])]


def _due_text(due: str) -> str:
    return f" (마감 {int(due[5:7])}/{int(due[8:10])})" if due and len(due) == 10 else ""


def homework_new(hid: int) -> int:
    with session_scope() as db:
        h = db.get(Homework, hid)
        if h is None:
            return 0
        ids, title, due = [s.id for s in _hw_students(db, h)], h.title, h.due
    return notify_students(ids, "📚 새 숙제가 왔어요", f"{title}{_due_text(due)}", lambda st: f"/s/{st.access_code}/hw/{hid}")


def homework_remind(hid: int) -> int:
    with session_scope() as db:
        h = db.get(Homework, hid)
        if h is None:
            return 0
        ids, title, due = not_done_ids(db, h), h.title, h.due
    tail = "내일까지예요!" if due == (datetime.now(KST) + timedelta(days=1)).strftime("%Y-%m-%d") else "아직 안 끝났어요."
    return notify_students(ids, "⏰ 숙제 잊지 않았죠?", f"{title} — {tail}", lambda st: f"/s/{st.access_code}/hw/{hid}")


def _reminder_loop(stop: threading.Event):
    """마감 전날 17시~22시 사이에 한 번, 안 한 학생에게 알림."""
    while not stop.wait(15 * 60):
        try:
            t = datetime.now(KST)
            if not 17 <= t.hour < 22:
                continue
            tomorrow = (t + timedelta(days=1)).strftime("%Y-%m-%d")
            with session_scope() as db:
                hs = list(db.execute(select(Homework).where(Homework.due == tomorrow, Homework.active.is_(True))).scalars())
                todo = []
                for h in hs:
                    if (h.options or {}).get("reminded") != tomorrow:
                        h.options = {**(h.options or {}), "reminded": tomorrow}
                        todo.append(h.id)
            for hid in todo:
                homework_remind(hid)
        except Exception:  # noqa: BLE001
            log.exception("reminder loop")


_stop = threading.Event()


def start():
    _stop.clear()
    threading.Thread(target=_reminder_loop, args=(_stop,), name="push-reminder", daemon=True).start()


def stop():
    _stop.set()
