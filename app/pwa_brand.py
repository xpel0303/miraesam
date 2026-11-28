"""홈 화면 앱 이름·아이콘을 학원에 맞게 (학원 이름, 학원 로고가 있으면 로고 아이콘)."""
import base64
import hashlib
import io

_cache: dict[tuple, bytes] = {}


def names(academy, kind: str, student_name: str = "") -> tuple[str, str]:
    """kind: teacher | student | parent → (긴 이름, 짧은 이름 12자)."""
    an = ((academy.name if academy else "") or "미래샘").strip()
    if kind == "student":
        return f"{an} 숙제", (f"{an} 숙제" if len(an) <= 9 else an)[:12]
    if kind == "parent":
        return f"{an} · {student_name} 학습관리", f"{an} {student_name}"[:12]
    return f"{an} (선생님)", (f"{an} 교사" if len(an) <= 9 else an)[:12]


def _src(academy) -> str:
    return (getattr(academy, "app_icon", None) or getattr(academy, "logo", None) or "") if academy else ""


def icon_url(academy, size: int) -> str:
    if _src(academy):
        v = hashlib.md5(_src(academy).encode()).hexdigest()[:8]
        return f"/app-icon/{academy.id}-{size}.png?v={v}"
    return f"/static/icons/icon-{size}.png"


def icons(academy) -> list[dict]:
    return [{"src": icon_url(academy, 192), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon_url(academy, 512), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon_url(academy, 512), "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]


def render_icon(academy, size: int) -> bytes | None:
    """학원 로고(data URL) → 흰 바탕 정사각 PNG (가운데 72%, 둥근 마스크에도 안 잘리게)."""
    src = _src(academy)
    if "," not in src:
        return None
    key = (academy.id, size, hashlib.md5(src.encode()).hexdigest())
    if key in _cache:
        return _cache[key]
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1]))).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    full = bool(getattr(academy, "app_icon", None))  # 앱 아이콘으로 따로 올린 그림은 꽉 차게(이미 여백 포함)
    box = int(size * (0.92 if full else 0.72))
    img.thumbnail((box, box), Image.LANCZOS)
    if img.width < box and img.height < box:  # 작은 로고는 키움
        r = box / max(img.width, img.height)
        img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    _cache[key] = out.getvalue()
    return _cache[key]
