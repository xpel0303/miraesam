"""문법뱅크: 가져오기(JSON → DB)와 시험지에 넣을 문항 정리."""
import json
import re

from markupsafe import Markup, escape

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import BankQuestion

FIELDS = ["id", "cat", "subcat", "level", "book", "chapter", "unit", "qtype", "instr", "passage", "stem", "choices", "answer", "expl", "src"]
LEVEL_ORDER = {"하": 0, "중": 1, "상": 2}
CIRCLED = "①②③④⑤⑥⑦"


def import_rows(db: Session, rows: list[dict]) -> int:
    """공용 문항 전체를 새로 넣음. 파일 순서가 곧 정렬 순서(ord)."""
    # 검수한 문항은 다시 가져와도 고친 내용과 상태를 그대로 유지
    kept = {r.id: r for r in db.execute(select(BankQuestion).where(BankQuestion.review != "")).scalars()}
    kept = {k: {f: getattr(v, f) for f in ("instr", "passage", "stem", "choices", "answer", "expl", "review")} for k, v in kept.items()}
    db.execute(delete(BankQuestion))
    batch, seen, n = [], set(), 0
    for i, r in enumerate(rows):
        rid = str(r.get("id") or "").strip()
        if not rid or rid in seen or not any(str(r.get(k) or "").strip() for k in ("stem", "choices", "instr")):
            continue
        seen.add(rid)
        d = {k: ("" if r.get(k) is None else str(r.get(k))) for k in FIELDS}
        d["no"] = str(r.get("no", ""))[:12]
        d["page"] = r.get("page") if isinstance(r.get("page"), int) else None
        d["ord"] = i
        d["id"], d["cat"] = rid[:40], d["cat"][:60]
        d["review"] = ""
        if rid in kept:
            d.update(kept[rid])
        for k, w in (("subcat", 120), ("book", 80), ("chapter", 120), ("unit", 160), ("qtype", 30), ("src", 20)):
            d[k] = d[k][:w]
        batch.append(d)
        n += 1
        if len(batch) >= 1000:
            db.execute(BankQuestion.__table__.insert(), batch)
            batch = []
    if batch:
        db.execute(BankQuestion.__table__.insert(), batch)
    db.commit()
    return n


def import_file(db: Session, path: str) -> int:
    with open(path, encoding="utf-8") as f:
        return import_rows(db, json.load(f))


def facets(db: Session) -> dict:
    """필터 목록: 대분류 → 세부(문항 수), 교재, 정답 출처, 유형."""
    cats, subs = {}, {}
    for cat, sub, n, o in db.execute(select(BankQuestion.cat, BankQuestion.subcat, func.count(), func.min(BankQuestion.ord)).group_by(BankQuestion.cat, BankQuestion.subcat).order_by(func.min(BankQuestion.ord))):
        cats[cat] = cats.get(cat, 0) + n
        subs.setdefault(cat, []).append((sub, n))
    col = lambda c: [(v, n) for v, n in db.execute(select(c, func.count()).group_by(c).order_by(c)) if v]
    return {"cats": cats, "subs": subs, "books": col(BankQuestion.book), "srcs": col(BankQuestion.src), "qtypes": col(BankQuestion.qtype)}


def apply_filters(stmt, f: dict):
    for key, col in (("cat", BankQuestion.cat), ("subcat", BankQuestion.subcat), ("level", BankQuestion.level), ("book", BankQuestion.book), ("src", BankQuestion.src), ("qtype", BankQuestion.qtype)):
        if f.get(key):
            stmt = stmt.where(col == f[key])
    if f.get("status") == "todo":
        stmt = stmt.where(BankQuestion.review == "")
    elif f.get("status") in ("ok", "fixed", "del"):
        stmt = stmt.where(BankQuestion.review == f["status"])
    else:
        stmt = stmt.where(BankQuestion.review != "del")
    if f.get("q"):
        stmt = stmt.where(BankQuestion.stem.contains(f["q"]))
    return stmt


_NUM = re.compile(r"\s+(?=[①②③④⑤⑥⑦])")
_LET = re.compile(r"\s+(?=[ⓐⓑⓒⓓⓔⓕ])")
_ABC = re.compile(r"\s+(?=[a-e]\.\s)")
BLANK = "_" * 14  # 빈칸은 충분히 길게
CHOICE_MARKS = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5, "ⓐ": 1, "ⓑ": 2, "ⓒ": 3, "ⓓ": 4, "ⓔ": 5,
                "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}


def _break_choices(text: str) -> str:
    """한 줄로 붙어 있는 ①②③④⑤ / ⓐⓑⓒⓓⓔ / a. b. c. 보기를 줄마다 나눔."""
    t = (text or "").strip()
    t = re.sub(r"_{2,}", BLANK, t)  # 짧은 빈칸 늘리기
    t = re.sub(r"\s*\|\s*(?=[①②③④⑤⑥⑦ⓐⓑⓒⓓⓔⓕ])", " ", t)  # 보기 사이 구분선 제거
    if "\n" in t:
        return t
    inline_marks = re.search(r"[①②③④⑤]\s*〔", t)  # 문장 속 밑줄 번호(① 〔are〕 …)는 보기가 아니므로 줄을 나누지 않음
    t = _NUM.sub("\n", t) if t.count("①") and t.count("②") and not inline_marks else t
    t = _LET.sub("\n", t) if t.count("ⓐ") and t.count("ⓑ") else t
    if re.search(r"(^|\s)a\.\s.*\sb\.\s.*\sc\.\s", t):
        t = _ABC.sub("\n", t)
    return t.strip()


def rich(text) -> Markup:
    """밑줄 〔…〕 은 밑줄로, 〔A / B〕 처럼 고르는 칸은 [A / B] 로 바꿔 화면·PDF에 보임."""
    def sub(m):
        inner = m.group(1).strip()
        return f"[{inner}]" if "/" in inner else f"<u>{inner}</u>"
    html = re.sub(r"〔(.+?)〕", sub, str(escape(text or "")))
    html = html.replace(BLANK, '<span style="display:inline-block;min-width:7em;border-bottom:0.8pt solid currentColor"></span>')
    return Markup(html)


def segments(text: str) -> list[tuple[str, bool]]:
    """워드용: 〔…〕 부분은 밑줄 글자로 나눔 (고르는 칸 / 는 대괄호)."""
    out = []
    for part in re.split(r"(〔.+?〕)", text or ""):
        if not part:
            continue
        if part.startswith("〔") and part.endswith("〕"):
            inner = part[1:-1].strip()
            out.append((f"[{inner}]", False) if "/" in inner else (inner, True))
        else:
            out.append((part, False))
    return out


def choice_number(answer: str) -> int | None:
    """정답이 보기 하나(① ⓒ 3 …)면 1~5 번호, 아니면 None."""
    a = (answer or "").strip().rstrip(".)")
    return CHOICE_MARKS.get(a)


def is_objective(stem: str, choices: str) -> bool:
    return bool(choices.strip()) or bool(re.search(r"[①②③④⑤]", stem)) or bool(re.search(r"(^|\n)[a-e]\.\s", stem)) or bool(re.search(r"[ⓐⓑⓒⓓⓔ]", stem))


_SPLIT = re.compile(r"^(?P<head>[^A-Za-z〔]*?[가-힣][^A-Za-z〔]*?(?:시오|세요|하라|라|까|요|오)[.?:]|[^A-Za-z〔]*?[가-힣]\s*\?)\s+(?=[A-Z〔\"“'‘(\[])")


def split_head(stem: str) -> tuple[str, str]:
    """'발문 + 영어 지문/문장'이 한 줄로 붙은 본문을 발문과 지문으로 나눔."""
    m = _SPLIT.match(stem or "")
    if m and len(m.group("head")) >= 8 and len(stem) - m.end() >= 8:
        return m.group("head").strip(), stem[m.end():].strip()
    return "", stem


_INSTR_SPLIT = re.compile(r"^((?:[^A-Za-z]|(?<![A-Za-z])[OXTF](?![A-Za-z])){6,}?[가-힣\)\]〉>][.?](?:\s*[\(（][^)）]{1,30}[\)）])?)\s+(?=[A-Za-z0-9\[〔“\"‘']|\([A-Za-z])")


def split_instr(text: str, min_rest: int = 12) -> tuple[str, str]:
    """'한글 발문. English …' 처럼 붙어 있으면 발문과 나머지(지문·문장)로 나눔."""
    m = _INSTR_SPLIT.match((text or "").strip())
    if m:
        rest = text.strip()[m.end():].strip()
        if len(rest) >= min_rest:
            return m.group(1).strip(), rest
    return (text or "").strip(), ""


def display(q: BankQuestion) -> dict:
    """시험지에 찍을 모양: 발문·지문·본문·보기를 한 번씩만, 보기는 한 줄에 하나."""
    clean = lambda t: re.sub(r"\s*\(추정\)\s*$", "", (t or "").strip())
    stem, instr = _break_choices(q.stem), clean(q.instr)
    head = instr if instr and instr not in (q.stem or "") and not (q.stem or "").startswith(instr[:30]) else ""
    raw = (q.stem or "").strip()
    if instr and raw.startswith(instr) and len(raw) - len(instr) >= 3:  # 발문이 본문 앞에 그대로 붙은 경우
        head, stem = instr, _break_choices(raw[len(instr):].strip())
    elif not head or raw.startswith(instr[:30]):
        h2, rest = split_head(raw)
        if h2:
            head, stem = h2, _break_choices(rest)
    passage = (q.passage or "").strip()
    h3, rest3 = split_head(instr)  # 발문 뒤에 지문이 붙어 있는 경우: 발문 / 지문 분리
    if h3 and len(rest3) >= 20:
        head = h3
        passage = (passage + "\n" + rest3).strip() if passage else rest3
    head = re.sub(r"_{2,}", BLANK, head)
    passage = re.sub(r"_{2,}", BLANK, (q.passage or "").strip())
    if head:  # 발문에 지문이 붙어 있으면 따로 나눔
        head, rest = split_instr(head)
        if rest:
            passage = (_break_choices(rest) + ("\n" + passage if passage else "")).strip()
    elif stem and not passage:  # 본문 앞머리가 발문이면 발문과 본문을 나눔
        h2, rest = split_instr(stem)
        if rest:
            head, stem = h2, _break_choices(rest)
    choices = _break_choices(q.choices)
    if choices and choices[:20] in stem:
        choices = ""
    answer = (q.answer or "").strip()
    objective = is_objective(stem, choices)
    lines = 0 if objective else (2 if len(answer) > 45 else 1)
    return {"id": q.id, "head": head, "passage": re.sub(r"_{2,}", BLANK, passage), "stem": stem, "choices": choices,
            "answer": answer, "expl": (q.expl or "").strip(), "src": q.src, "cat": q.cat, "subcat": q.subcat,
            "level": q.level, "review": q.review, "book": q.book, "page": q.page, "objective": objective, "lines": lines,
            "raw_stem": q.stem or "", "raw_instr": q.instr or "", "raw_choices": q.choices or ""}


def split_lines(text: str) -> list[str]:
    return [t for t in re.split(r"\n+", text) if t.strip()]
