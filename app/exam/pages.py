"""시험지 PDF·사진 → 쪽별 이미지 (방향 보정).

- 스캔 PDF는 회전값(/Rotate)이 잘못 들어 있는 경우가 많아서(받은 시험지 13개 중 7개),
  그림으로 그린 뒤 tesseract 방향 감지(OSD)로 글자가 바로 서도록 돌립니다.
- tesseract 가 없거나 확신이 낮으면 그대로 둡니다 (선생님이 화면에서 돌릴 수 있음).
- 600dpi 스캔은 너무 커서, 가로 1700px 회색조 JPEG로 줄여 저장합니다 (Gemini에도 충분한 해상도).
"""
import io
import re
import shutil
import subprocess
import tempfile

from PIL import Image

WIDTH = 1700
MAX_PAGES = 16
OSD_MIN_CONF = 3.0


def render_pdf(data: bytes) -> list[Image.Image]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    if len(doc) > MAX_PAGES:
        raise ValueError(f"쪽이 너무 많아요 ({len(doc)}쪽). 시험지는 {MAX_PAGES}쪽까지 올릴 수 있어요.")
    out = []
    for page in doc:
        w, h = page.get_size()
        scale = WIDTH / min(w, h)  # 가로·세로 어느 쪽으로 누워 있어도 짧은 변이 WIDTH
        out.append(page.render(scale=scale, grayscale=True).to_pil())
    return out


def open_image(data: bytes) -> Image.Image:
    from PIL import ImageOps

    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    return im.convert("L")


def detect_rotation(im: Image.Image) -> tuple[int, float]:
    """(시계 방향으로 돌려야 할 각도, 확신도). tesseract 가 없으면 (0, 0)."""
    if not shutil.which("tesseract"):
        return 0, 0.0
    small = im.copy()
    small.thumbnail((2400, 2400))  # 작게 줄이면 방향을 자주 틀림 (1100px: 확신도 1 안팎, 2400px: 15 이상)
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        small.save(f.name)
        try:
            r = subprocess.run(["tesseract", f.name, "-", "--psm", "0"], capture_output=True, text=True, timeout=40)
        except (subprocess.TimeoutExpired, OSError):
            return 0, 0.0
    m = re.search(r"Rotate: (\d+)", r.stdout)
    c = re.search(r"Orientation confidence: ([\d.]+)", r.stdout)
    return (int(m.group(1)) if m else 0), (float(c.group(1)) if c else 0.0)


def rotate(im: Image.Image, deg: int) -> Image.Image:
    deg %= 360
    return im.rotate(-deg, expand=True) if deg else im


def normalize(im: Image.Image) -> Image.Image:
    """짧은 변 WIDTH 로 맞춤."""
    w, h = im.size
    s = WIDTH / min(w, h)
    if abs(s - 1) > 0.02:
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    return im


def ink_ratio(im: Image.Image) -> float:
    """어두운 점 비율. 받은 시험지 103쪽 기준: 빈 쪽 0.1% 이하, 글이 있는 쪽 1.5% 이상."""
    small = im.copy()
    small.thumbnail((800, 800))
    hist = small.convert("L").histogram()
    return sum(hist[:170]) / max(1, sum(hist))


def to_jpeg(im: Image.Image, quality: int = 78) -> bytes:
    buf = io.BytesIO()
    im.convert("L").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def prepare(files: list[tuple[str, bytes]], step=lambda *_: None) -> list[dict]:
    """[(파일 이름, 내용)] → [{jpeg, rot, conf, w, h}] (올린 순서대로 이어 붙임)."""
    raw: list[Image.Image] = []
    for name, data in files:
        if name.lower().endswith(".pdf") or data[:4] == b"%PDF":
            raw += render_pdf(data)
        else:
            raw.append(open_image(data))
        if len(raw) > MAX_PAGES:
            raise ValueError(f"쪽이 너무 많아요. 시험지는 {MAX_PAGES}쪽까지 올릴 수 있어요.")
    out = []
    for i, im in enumerate(raw, 1):
        step(f"{i}/{len(raw)}쪽 방향 맞추는 중", 5 + int(25 * i / max(1, len(raw))))
        deg, conf = detect_rotation(im)
        if conf < OSD_MIN_CONF:
            deg = 0
        im = normalize(rotate(im, deg))
        out.append({"jpeg": to_jpeg(im), "rot": deg, "conf": round(conf, 2), "w": im.width, "h": im.height,
                    "blank": ink_ratio(im) < 0.003})
    return out
