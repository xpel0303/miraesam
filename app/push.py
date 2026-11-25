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
    from .homework.spec import for_student
    return [s for s in db.execute(select(Student).where(Student.class_id.in_(cids), Student.active.is_(True))).scalars() if for_student(h, s.id)]


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


# ---------------------------------------------------------------- 학부모 앱 알림 (학습관리)
# 학부모 휴대폰(/p/ 학부모 앱)으로 수업 기록·공지 알림. 구독은 parent_push_subs, 보낸 기록은 push_logs.
# 암호화(RFC 8291 aes128gcm)·VAPID 서명(RFC 8292)을 cryptography·httpx 로 직접 함 (보낸 결과·실패 이유를 기록하려고).
# VAPID 키는 숙제 알림과 같은 것(app_secrets 'vapid')을 씀.
import os as _os  # noqa: E402
import struct as _struct  # noqa: E402
from urllib.parse import urlparse as _urlparse  # noqa: E402

SITE_URL = _os.getenv("SITE_URL", "https://miraesam.onrender.com")  # VAPID 'sub' (푸시 서버가 연락처로 봄)
_priv = None


def b64u(data: bytes) -> str:
    return _b64(data)


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_key() -> str:
    return keys()["public"]


def _private():
    global _priv
    if _priv is None:
        from cryptography.hazmat.primitives import serialization
        _priv = serialization.load_pem_private_key(keys()["private_pem"].encode(), password=None)
    return _priv


def _vapid_header(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = _urlparse(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SITE_URL},
                             separators=(",", ":")).encode())
    r, s = decode_dss_signature(_private().sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={public_key()}"


def _hmac(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, hmac
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def encrypt(payload: bytes, p256dh: str, auth: str, *, _salt: bytes | None = None, _eph=None) -> bytes:
    """RFC 8291 aes128gcm 한 레코드."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_public, auth_secret = unb64u(p256dh), unb64u(auth)
    eph = _eph or ec.generate_private_key(ec.SECP256R1())
    as_public = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ecdh = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hmac(_hmac(auth_secret, ecdh), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = _salt or _os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + _struct.pack("!IB", 4096, len(as_public)) + as_public + cipher


def send_one(client, sub, data: dict) -> tuple[bool, bool, str]:
    """(성공, 구독이 죽었음, 오류 글)."""
    import httpx
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
    try:
        r = client.post(sub.endpoint, content=body, headers={
            "Authorization": _vapid_header(sub.endpoint), "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream", "TTL": str(3 * 24 * 3600), "Urgency": "normal"})
    except httpx.HTTPError as e:
        return False, False, f"연결 실패: {type(e).__name__}"
    if r.status_code in (200, 201, 202):
        return True, False, ""
    return False, r.status_code in (404, 410), f"{r.status_code} {r.text[:200]}"


def deliver(academy_id: int, messages: list[dict], kind: str) -> None:
    """messages: [{"student_id", "title", "body", "url"}]. 학생마다 등록된 학부모 휴대폰 모두에 보내고 기록을 남김."""
    import httpx
    from .db import SessionLocal
    from .models import ParentPushSub, PushLog, now
    db = SessionLocal()
    try:
        with httpx.Client(timeout=15) as client:
            for m in messages:
                subs = list(db.execute(select(ParentPushSub).where(ParentPushSub.student_id == m["student_id"])).scalars())
                ok_n, errors = 0, []
                for sub in subs:
                    ok, gone, err = send_one(client, sub, {"title": m["title"], "body": m["body"], "url": m.get("url", "/"), "tag": m.get("url", "")})
                    if ok:
                        ok_n += 1
                        sub.fails, sub.last_ok_at = 0, now()
                    else:
                        errors.append(err)
                        sub.fails = (sub.fails or 0) + 1
                        if gone or sub.fails >= 5:
                            db.delete(sub)  # 앱을 지웠거나 알림을 끈 휴대폰
                status = "sent" if ok_n else ("fail" if subs else "none")
                db.add(PushLog(academy_id=academy_id, student_id=m["student_id"], kind=kind, title=m["title"][:120], body=m["body"],
                               status=status, devices=ok_n, error=" / ".join(errors)[:1000]))
                db.commit()
    finally:
        db.close()


def deliver_later(academy_id: int, messages: list[dict], kind: str) -> None:
    """화면은 바로 돌려주고 뒤에서 보냄."""
    if messages:
        in_background(deliver, academy_id, messages, kind)
