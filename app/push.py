"""웹 푸시: 학부모 휴대폰(홈 화면에 추가한 학부모 앱)으로 알림 보내기.

카톡·문자 업체 없이 무료로 보냅니다. 표준(RFC 8291 aes128gcm 암호화 + RFC 8292 VAPID) 그대로,
이미 쓰는 cryptography·httpx 만으로 구현했습니다 (pywebpush 는 설치가 잘 안 돼서 쓰지 않음).

VAPID 키는 서버가 처음 쓸 때 스스로 만들어 DB(app_kv)에 둡니다. 환경변수는 필요 없습니다.
키가 바뀌면 이미 받은 구독이 모두 무효가 되니, app_kv 의 vapid_private 은 지우지 마세요.
"""
import base64
import json
import os
import struct
import threading
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from .db import SessionLocal
from .models import AppKV, PushLog, PushSub, now

SITE_URL = os.getenv("SITE_URL", "https://miraesam.onrender.com")  # VAPID 'sub' (푸시 서버가 연락처로 봄)
_lock = threading.Lock()
_keys: tuple[ec.EllipticCurvePrivateKey, str] | None = None


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_keys() -> tuple[ec.EllipticCurvePrivateKey, str]:
    """(개인키, 공개키 base64url). 없으면 만들어 DB에 저장."""
    global _keys
    if _keys:
        return _keys
    with _lock:
        if _keys:
            return _keys
        db = SessionLocal()
        try:
            row = db.get(AppKV, "vapid_private")
            if row is None:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
                db.add(AppKV(key="vapid_private", value=pem))
                try:
                    db.commit()
                except Exception:  # 다른 작업자가 먼저 만듦
                    db.rollback()
                    row = db.get(AppKV, "vapid_private")
            if row is not None:
                key = serialization.load_pem_private_key(row.value.encode(), password=None)
        finally:
            db.close()
        _keys = (key, b64u(_raw_public(key)))
        return _keys


def public_key() -> str:
    return vapid_keys()[1]


def _vapid_header(endpoint: str) -> str:
    key, pub = vapid_keys()
    u = urlparse(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SITE_URL},
                             separators=(",", ":")).encode())
    signing = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={header}.{claims}.{b64u(sig)}, k={pub}"


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def encrypt(payload: bytes, p256dh: str, auth: str, *, _salt: bytes | None = None, _eph: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291 aes128gcm 한 레코드."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    eph = _eph or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(eph)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = eph.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, ecdh)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = _salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + cipher


def send_one(client: httpx.Client, sub: PushSub, data: dict) -> tuple[bool, bool, str]:
    """(성공, 구독이 죽었음, 오류 글)."""
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
    try:
        r = client.post(sub.endpoint, content=body, headers={
            "Authorization": _vapid_header(sub.endpoint), "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream", "TTL": str(3 * 24 * 3600), "Urgency": "normal"})
    except httpx.HTTPError as e:
        return False, False, f"연결 실패: {type(e).__name__}"
    if r.status_code in (200, 201, 202):
        return True, False, ""
    gone = r.status_code in (404, 410)
    return False, gone, f"{r.status_code} {r.text[:200]}"


def deliver(academy_id: int, messages: list[dict], kind: str) -> None:
    """messages: [{"student_id", "title", "body", "url"}]. 학생마다 등록된 휴대폰 모두에 보내고 기록을 남김."""
    db = SessionLocal()
    try:
        with httpx.Client(timeout=15) as client:
            for m in messages:
                subs = list(db.execute(select(PushSub).where(PushSub.student_id == m["student_id"])).scalars())
                ok_n, errors = 0, []
                for sub in subs:
                    ok, gone, err = send_one(client, sub, {"title": m["title"], "body": m["body"], "url": m.get("url", "/")})
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
        threading.Thread(target=deliver, args=(academy_id, messages, kind), daemon=True).start()
