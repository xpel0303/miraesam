"""지문 보관함 저장 도우미."""
import hashlib
import re

from sqlalchemy import select

from ..models import Passage, Teacher

MIN_LEN = 40  # 이보다 짧은 글(문장 하나 등)은 저장하지 않음


def text_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text or "").strip().lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def remember(db, teacher: Teacher, items: list[tuple[str, str]], source: str = "") -> int:
    """(제목, 본문) 목록을 보관함에 저장. 같은 글이 있으면 사용 횟수만 올리고 제목이 비었으면 채움. 새로 저장한 개수."""
    new = 0
    for title, text in items:
        text = (text or "").strip()
        if len(text) < MIN_LEN:
            continue
        h = text_hash(text)
        p = db.execute(select(Passage).where(Passage.academy_id == teacher.academy_id, Passage.text_hash == h)).scalars().first()
        if p is None:
            p = Passage(academy_id=teacher.academy_id, teacher_id=teacher.id, title=(title or "").strip()[:160] or _auto_title(text),
                        text=text, source=source[:120], text_hash=h, used=0)
            db.add(p)
            new += 1
        elif title and title.strip() and not p.title:
            p.title = title.strip()[:160]
        p.used = (p.used or 0) + 1
    db.commit()
    return new


def remember_form(db, teacher: Teacher, f) -> int:
    """자료 만들기 화면의 폼에서 지문을 꺼내 저장 (p_title/p_text 묶음 또는 passage 칸)."""
    try:
        titles, texts = f.getlist("p_title"), f.getlist("p_text")
        items = [(titles[i] if i < len(titles) else "", t) for i, t in enumerate(texts)]
        if not items:
            items = [("", t) for t in f.getlist("passage")]
            if len(items) == 1 and f.get("title"):
                items = [(str(f.get("title")), items[0][1])]
        return remember(db, teacher, [(str(a), str(b)) for a, b in items])
    except Exception:  # noqa: BLE001  — 보관함 저장이 자료 만들기를 막으면 안 됨
        db.rollback()
        return 0


def _auto_title(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return (first[:60] + "…") if len(first) > 60 else first


# ---------------------------------------------------------------- 번호 붙은 지문 여러 개를 한 번에 붙여 넣을 때

_HEAD = re.compile(r"^\s*(?:\[\s*(\d{1,3}(?:\s*[~\-–]\s*\d{1,3})?)\s*\]|(\d{1,3}(?:\s*[~\-–]\s*\d{1,3})?)\s*(?:번|\.|\)|번\.)(?=\s|$))\s*(.*)$")
_CHOICE = re.compile(r"^\s*[①②③④⑤]")


def num_key(title: str) -> tuple:
    """'18번', '[20~21]', '2025 고2 3월 20번' → 정렬 열쇠 (번호 없는 것은 뒤로)."""
    m = re.search(r"(\d{1,3})(?:\s*[~\-–]\s*\d{1,3})?\s*번", title or "") or re.match(r"\s*\[?(\d{1,3})", title or "")
    return (0, int(m.group(1)), title) if m else (1, 0, title or "")


def _is_korean_line(line: str) -> bool:
    ko = len(re.findall(r"[가-힣]", line))
    en = len(re.findall(r"[A-Za-z]", line))
    return ko >= 4 and ko > en


def split_numbered(text: str) -> list[tuple[str, str]]:
    """'18. 다음 글의 목적으로… Dear Mr. Kim, …' 처럼 번호로 시작하는 지문이 여러 개면 번호마다 나눔.
    번호 머리가 2개 미만이면 [] (나누지 않음). 우리말 발문 줄, ①~⑤ 선택지 줄은 뺌. 번호 순서로 정렬."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    heads = [(i, _HEAD.match(ln)) for i, ln in enumerate(lines)]
    heads = [(i, m) for i, m in heads if m]
    if len(heads) < 2:
        return []
    out = []
    for k, (i, m) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        num = re.sub(r"\s+", "", m.group(1) or m.group(2)).replace("–", "~").replace("-", "~")
        body = [m.group(3)] + lines[i + 1:end]
        keep = [ln for ln in body if ln.strip() and not _CHOICE.match(ln) and not _is_korean_line(ln)]
        txt = "\n".join(ln.strip() for ln in keep).strip()
        if txt:
            out.append((f"{num}번", txt))
    out.sort(key=lambda x: num_key(x[0]))
    return out
