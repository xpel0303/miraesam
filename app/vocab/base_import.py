"""기본 단어장 올리기: 엑셀(.xlsx, 또는 그걸 묶은 .zip)의 '단어장' 시트 → 단어장.

시트 모양 (원장님 단계별 단어장): 1행 제목, 4행 머리글 Day | 번호 | 표제어 | 품사 | 뜻 | 교과서 예문 | …
Day 는 단어의 src("Day 01")로 넣어서, 시험지·숙제의 '지문별 고르기'가 그대로 'Day 고르기'가 됨.
"""
import io
import re
import zipfile

LEVEL_WORDS = [("초등", "초등"), ("중학", "중등"), ("중1", "중등"), ("고1", "고1"), ("고2", "고2"), ("고3", "고3")]


def _cell(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def parse_xlsx(data: bytes, filename: str = "") -> dict | None:
    """→ {title, level, words:[{word,pos,meaning,example,src}], days} 또는 None(모양이 다름)."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb["단어장"] if "단어장" in wb.sheetnames else wb[wb.sheetnames[0]]
    title, head, words, days = "", None, [], []
    for row in ws.iter_rows(values_only=True):
        cells = [_cell(v) for v in row]
        if not any(cells):
            continue
        if head is None:
            if "표제어" in cells or "단어" in cells:
                head = {name: i for i, name in enumerate(cells) if name}
            elif not title:
                title = cells[0]
            continue
        get = lambda *names: next((cells[head[n]] for n in names if n in head and head[n] < len(cells) and cells[head[n]]), "")
        word = get("표제어", "단어")
        if not word:
            continue
        day = get("Day", "DAY", "day", "일차")
        m = re.search(r"\d+", day)
        src = f"Day {int(m.group()):02d}" if m else ""
        if src and src not in days:
            days.append(src)
        words.append({"word": word, "pos": get("품사"), "meaning": get("뜻", "의미"),
                      "example": re.sub(r"^[★☆*]\s*", "", get("교과서 예문", "예문")), "src": src})
    wb.close()
    if head is None or not words:
        return None
    name = re.sub(r"\.xlsx$", "", filename.rsplit("/", 1)[-1], flags=re.I)
    level = next((lv for k, lv in LEVEL_WORDS if k in (title + name)), "")
    return {"title": (title or name)[:120], "level": level, "words": words, "days": days}


def read_upload(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """올린 파일 → [(이름, xlsx 바이트)]. zip 이면 안의 xlsx 를 다 꺼냄 (이름 순서대로)."""
    if filename.lower().endswith(".zip") or data[:2] == b"PK" and not filename.lower().endswith(".xlsx"):
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                raw = info.filename
                if not (info.flag_bits & 0x800):  # 윈도우 압축(cp949) 이름 살리기
                    for enc in ("utf-8", "cp949"):
                        try:
                            raw = info.filename.encode("cp437").decode(enc)
                            break
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            continue
                base = raw.rsplit("/", 1)[-1]
                if base.lower().endswith(".xlsx") and not base.startswith(("~$", "._")) and info.file_size < 30_000_000:
                    out.append((base, z.read(info)))
        return sorted(out)
    return [(filename, data)]
