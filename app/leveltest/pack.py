"""레벨테스트 시험지 꾸러미 읽기.

꾸러미 = 폴더마다 test.json({title, sub, sort, minutes, show_result, active, spec}) + 듣기 파일(audio.*) + images/그림.png|jpg
- 선생님이 zip으로 올리는 경우 (routes/leveltest.py import_zip)
- 사이트에 처음부터 들어 있는 시험지 (app/leveltest/builtin/) — 서버가 켜질 때 학원마다 자동으로 넣음
"""
import json
import logging
import threading
from pathlib import Path

from sqlalchemy import delete, select

from ..models import Academy, LevelTest, StoredFile
from . import spec as lspec

log = logging.getLogger(__name__)
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg", ".aac": "audio/aac"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}
BUILTIN_DIR = Path(__file__).parent / "builtin"


def roots_of(names: list[str]) -> list[str]:
    return sorted({n.rsplit("test.json", 1)[0] for n in names if n.endswith("test.json")})


def load_one(db, academy_id: int, root: str, names: list[str], read, existing: dict, builtin: str = "") -> tuple[str | None, str | None]:
    """폴더 하나를 시험지로. 같은 이름이 있으면 문항·듣기·그림만 새것으로(응시 결과는 그대로). (완료 문구, 문제 문구)"""
    try:
        meta = json.loads(read(root + "test.json").decode("utf-8-sig"))
        spec, errs = lspec.check(meta.get("spec") or {})
    except (ValueError, KeyError) as e:
        return None, f"{root or 'test.json'}: 읽을 수 없어요 ({e})"
    title = str(meta.get("title") or root.strip("/") or "레벨테스트")[:80]
    if errs:
        return None, f"{title}: " + " / ".join(errs[:3])
    spec.pop("minutes", None)
    if builtin:
        spec["builtin"] = builtin
    t = existing.get(title)
    new = t is None
    if new:
        t = LevelTest(academy_id=academy_id, title=title, images={}, spec={})
        db.add(t)
    if new or not builtin:  # 사이트에 들어 있는 시험지는 선생님이 바꾼 설정(공개·시간·순서)을 건드리지 않음
        t.sub = str(meta.get("sub") or "")[:120]
        t.sort = int(meta.get("sort") or t.sort or 0)
        t.minutes = int(meta.get("minutes") or 0)
        t.show_result = bool(meta.get("show_result", True))
        t.active = bool(meta.get("active", False))
    t.spec = spec
    db.flush()
    old_ids = [x for x in [t.audio_file_id, *(t.images or {}).values()] if x]
    t.audio_file_id, images = None, {}
    for n in names:
        if not n.startswith(root) or n == root + "test.json":
            continue
        rel = n[len(root):]
        base, _, ext = rel.rpartition(".")
        ext = "." + ext.lower()
        if rel.startswith("images/") and ext in IMAGE_TYPES:
            sf = StoredFile(academy_id=academy_id, filename=rel[7:][:200], mime=IMAGE_TYPES[ext], data=read(n))
            db.add(sf)
            db.flush()
            images[base[7:]] = sf.id
        elif "/" not in rel and ext in AUDIO_TYPES:
            sf = StoredFile(academy_id=academy_id, filename=rel[:200], mime=AUDIO_TYPES[ext], data=read(n))
            db.add(sf)
            db.flush()
            t.audio_file_id = sf.id
    t.images = images
    if old_ids:
        db.execute(delete(StoredFile).where(StoredFile.id.in_(old_ids)))
    existing[title] = t
    missing = lspec.image_keys(spec) - set(images)
    n_items = len(lspec.items(spec))
    return f"{title} {n_items}문항" + (" · 듣기" if t.audio_file_id else "") + (f" (그림 없음: {', '.join(sorted(missing))})" if missing else ""), None


# ---------------------------------------------------------------- 사이트에 들어 있는 시험지

def _builtin_version() -> str:
    f = BUILTIN_DIR / "VERSION"
    return f.read_text().strip() if f.exists() else ""


def seed_builtin(db) -> int:
    """학원마다 기본 시험지가 없거나 옛 버전이면 넣음/새로 고침. 넣은 개수."""
    version = _builtin_version()
    if not version:
        return 0
    names = [p.relative_to(BUILTIN_DIR).as_posix() for p in BUILTIN_DIR.rglob("*") if p.is_file() and p.name != "VERSION"]
    roots = roots_of(names)
    read = lambda n: (BUILTIN_DIR / n).read_bytes()  # noqa: E731
    count = 0
    for academy_id in db.execute(select(Academy.id)).scalars():
        tests = list(db.execute(select(LevelTest).where(LevelTest.academy_id == academy_id)).scalars())
        existing = {t.title: t for t in tests}
        for root in roots:
            meta = json.loads(read(root + "test.json").decode("utf-8-sig"))
            t = existing.get(str(meta.get("title") or ""))
            if t is not None and (t.spec or {}).get("builtin") == version:
                continue
            if t is not None and (t.spec or {}).get("builtin") is None and t.spec:
                continue  # 선생님이 같은 이름으로 직접 만든 시험지는 건드리지 않음
            done, problem = load_one(db, academy_id, root, names, read, existing, builtin=version)
            if problem:
                log.warning("기본 레벨테스트 넣기 실패: %s", problem)
            else:
                count += 1
        db.commit()
    return count


_LOCK = threading.Lock()


def needs_seed(db, academy_id: int) -> bool:
    version = _builtin_version()
    if not version:
        return False
    return not any((t.spec or {}).get("builtin") == version
                   for t in db.execute(select(LevelTest).where(LevelTest.academy_id == academy_id)).scalars())


def seed_builtin_background():
    """서버 시작을 늦추지 않게 뒤에서 (듣기 파일이 커서 몇 초 걸림)."""
    from ..db import SessionLocal

    def run():
        if not _LOCK.acquire(blocking=False):
            return  # 이미 넣는 중
        try:
            with SessionLocal() as db:
                n = seed_builtin(db)
                if n:
                    log.info("기본 레벨테스트 %d개를 넣었어요.", n)
        except Exception:  # noqa: BLE001
            log.exception("기본 레벨테스트 넣기 실패")
        finally:
            _LOCK.release()

    threading.Thread(target=run, daemon=True, name="leveltest-seed").start()
