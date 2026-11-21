"""교과서 목표어법 목록 — 텍스트 ↔ 과 목록, 처음 쓰는 학원에 교과서 이름 넣어 두기."""
import re

SUBJECTS = ["공통영어1", "공통영어2", "영어I", "기타"]

# 2022 개정 검정 교과서 (공통영어1·2 10종, 영어I 8종). 과별 목표어법은 원장님 자료로 채움.
COMMON = ["능률(민병천)", "능률(오선영)", "YBM(박준언)", "YBM(김은형)", "천재(강상구)", "천재(조수경)",
          "비상(홍민표)", "미래엔(김성연)", "지학사(신상근)", "동아(이병민)"]
ENGLISH1 = ["능률(오선영)", "YBM(박준언)", "천재(강상구)", "천재(조수경)", "미래엔(김성연)", "동아(박용예)",
            "지학사(신상근)", "비상(홍민표)"]
DEFAULT_BOOKS = [("공통영어1", n) for n in COMMON] + [("공통영어2", n) for n in COMMON] + [("영어I", n) for n in ENGLISH1]


def lessons_to_text(lessons: list[dict]) -> str:
    return "\n".join(f"{l.get('name', '')}: {' / '.join(l.get('points') or [])}" for l in lessons or [])


def text_to_lessons(text: str) -> list[dict]:
    """'1과: 관계대명사 what / 분사구문' 한 줄에 한 과. ':' 가 없으면 과 이름만."""
    out = []
    for line in (text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        name, sep, rest = line.partition(":")
        if not sep:
            name, sep, rest = line.partition("|")
        pts = [re.sub(r"\s+", " ", p).strip(" ·•-") for p in re.split(r"\s*/\s*|•", rest)] if sep else []
        out.append({"name": name.strip()[:40], "points": [p[:80] for p in pts if p][:20]})
    return out[:30]
