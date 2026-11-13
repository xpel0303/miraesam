"""문법뱅크: 가져오기(JSON → DB)와 시험지에 넣을 문항 정리."""
import json
import re

from markupsafe import Markup, escape

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import BankQuestion

FIELDS = ["id", "cat", "subcat", "level", "book", "chapter", "unit", "qtype", "instr", "passage", "stem", "choices", "answer", "expl", "src", "tier", "sub_id"]
LEVEL_ORDER = {"하": 0, "중": 1, "상": 2}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def import_rows(db: Session, rows: list[dict], keep_reviewed: bool = True) -> int:
    """공용 문항 전체를 새로 넣음. 파일 순서가 곧 정렬 순서(ord).
    keep_reviewed: 검수한 문항은 고친 내용과 상태를 그대로 유지 (끄면 파일 내용으로 덮어씀, 파일의 review 값 사용)."""
    kept = {r.id: r for r in db.execute(select(BankQuestion).where(BankQuestion.review != "")).scalars()} if keep_reviewed else {}
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
        d["review"] = "" if keep_reviewed else str(r.get("review") or "")[:6]
        if rid in kept:
            d.update(kept[rid])
        for k, w in (("subcat", 120), ("book", 80), ("chapter", 120), ("unit", 160), ("qtype", 30), ("src", 20), ("tier", 4), ("sub_id", 12)):
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


def import_file(db: Session, path: str, keep_reviewed: bool = True) -> int:
    with open(path, encoding="utf-8") as f:
        return import_rows(db, json.load(f), keep_reviewed)


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


_INSTR_SPLIT = re.compile(r"^((?:[^A-Za-z]|(?<![A-Za-z])[OXTF](?![A-Za-z])){6,}?[가-힣\)\]〉>][.?](?:\s*[\(（][^)）]{1,30}[\)）])?)\s+(?=[A-Za-z0-9\[〔“\"‘']|\([A-Za-z])")


def split_instr(text: str, min_rest: int = 12) -> tuple[str, str]:
    """'한글 발문. English …' 처럼 붙어 있으면 발문과 나머지(지문·문장)로 나눔."""
    m = _INSTR_SPLIT.match((text or "").strip())
    if m:
        rest = text.strip()[m.end():].strip()
        if len(rest) >= min_rest:
            return m.group(1).strip(), rest
    return (text or "").strip(), ""


_KO_END = re.compile(r"(?<=[가-힣][.?!])\s+(?=[A-Z(\[〔“\"][A-Za-z])")
_MARK = re.compile(r"\s+(?=[〈<\[](?:보기|조건|예시)[〉>\]](?![가-힣]))")
_SPEAKER = re.compile(r"\s+(?=[A-Z]{1,2}:\s)")
_LET2NUM = dict(zip("ⓐⓑⓒⓓⓔ", "①②③④⑤"))


def structure(text: str) -> str:
    """한글 발문/해석 → 영어 문장, 〈보기〉〈조건〉, 대화 화자(A: B:)가 한 줄로 붙어 있으면 줄을 나눔."""
    out = []
    for line in (text or "").split("\n"):
        if len(re.findall(r"(?:^|\s)[A-Z]{1,2}:\s", line)) >= 2:
            line = _SPEAKER.sub("\n", line)
        for part in line.split("\n"):
            if re.search(r"[가-힣]", part) and not re.match(r"[A-Z]{1,2}:\s", part):  # 대화 한 줄 안은 나누지 않음
                part = _KO_END.sub("\n", part)
            part = _MARK.sub("\n", part)
            if re.match(r"[〈<\[]보기", part) and part.count("①") and part.count("②"):
                part = _NUM.sub("\n", part)
            if "〈조건〉" in part or "<조건>" in part:
                part = re.sub(r"\s+(?=[2-5]\.\s)", "\n", part)
            out.append(part)
    return "\n".join(out)


def circle_options(body: str, choices: str, answer: str) -> tuple[str, str, str]:
    """보기 번호가 ⓐ~ⓔ 인 선택지는 ①~⑤ 원 기호로 바꿈 (정답도 같이). 문장 이름표(ⓐ 문장)는 그대로."""
    def conv(t):
        return "".join(_LET2NUM.get(ch, ch) for ch in t)
    if choices and "ⓐ" in choices and "ⓑ" in choices and "ⓐ" not in body and "①" not in choices:
        return body, conv(choices), conv(answer)
    if not choices and "ⓐ" in body and "ⓑ" in body and "①" not in body:
        return conv(body), choices, conv(answer)
    return body, choices, answer


def display(q: BankQuestion) -> dict:
    """시험지에 찍을 모양: 발문 / 지문 / 문제 / 보기를 각각 따로 줄을 나눠서, 보기는 한 줄에 하나."""
    clean = lambda t: re.sub(r"\s*\(추정\)\s*$", "", (t or "").strip())
    raw_stem, instr = (q.stem or "").strip(), clean(q.instr)
    passage = (q.passage or "").strip()
    if instr and raw_stem.startswith(instr) and len(raw_stem) - len(instr) >= 3:  # 발문이 본문 앞에 그대로 붙은 경우
        head, body = instr, raw_stem[len(instr):].strip()
    elif instr and (instr in raw_stem or raw_stem.startswith(instr[:30])):
        head, body = "", raw_stem
    else:
        head, body = instr, raw_stem
    rest = ""
    if head:  # '한글 발문. English 지문…' 처럼 붙어 있으면 나눔
        head, rest = split_instr(head)
    else:
        head, body2 = split_instr(body)
        if body2:
            body = body2
        else:
            head = ""
    if rest:
        passage = (rest + ("\n" + passage if passage else "")).strip()
    if re.fullmatch(r"[_\s.]*", body or ""):  # 빈칸 표시만 남았으면 답 쓰는 줄이 대신함
        body = ""
    body, choices = _break_choices(body), _break_choices(q.choices)
    if choices and choices[:20] in body:
        choices = ""
    answer = (q.answer or "").strip()
    body, choices, answer = circle_options(body, choices, answer)
    head, body = structure(head), structure(body)
    passage = structure(passage)
    objective = is_objective(body, choices)
    lines = 0 if objective else (2 if len(answer) > 45 else 1)
    return {"id": q.id, "head": re.sub(r"_{2,}", BLANK, head), "passage": re.sub(r"_{2,}", BLANK, passage), "stem": body, "choices": choices,
            "answer": answer, "expl": (q.expl or "").strip(), "src": q.src, "cat": q.cat, "subcat": q.subcat,
            "level": q.level, "review": q.review, "book": q.book, "page": q.page, "objective": objective, "lines": lines,
            "raw_stem": q.stem or "", "raw_instr": q.instr or "", "raw_choices": q.choices or ""}


def group_same_passage(items: list[dict]) -> list[dict]:
    """같은 발문·지문을 쓰는 이어진 문항(지문 속 ①~⑤를 각각 고치는 문제 등)은 한 문제로 묶음.

    묶인 문제는 답 쓰는 줄이 소문항 수만큼 생기고, 정답은 '① went ② saw …' 로 나옵니다."""
    out = []
    for it in items:
        prev = out[-1] if out else None
        if prev and it["passage"] and len(it["passage"]) >= 40 and not it["objective"] and not it["stem"] and not it["choices"] \
                and prev["passage"] == it["passage"] and prev["head"] == it["head"] and not prev["objective"] and not prev["stem"] and not prev["choices"]:
            prev["subs"].append(it)
            prev["lines"] = len(prev["subs"])
            continue
        it = dict(it)
        it["subs"] = [it]
        out.append(it)
    for it in out:
        if len(it["subs"]) > 1:
            it["answer"] = "  ".join(f"{CIRCLED[i]} {x['answer']}" for i, x in enumerate(it["subs"]))
            it["merged"] = True
            it["src"] = "확인 필요" if any(x["src"] == "확인 필요" for x in it["subs"]) else it["src"]
    return out


def split_lines(text: str) -> list[str]:
    return [t for t in re.split(r"\n+", text) if t.strip()]


def pick_questions(db: Session, items: list[tuple[str, str]], levels: list[str], n_obj: int, n_sub: int, per_item: dict | None = None,
                   skip_unsure: bool = True, with_passage: bool = True) -> list[str]:
    """고른 항목(대분류·세부)에서 조건에 맞는 문항을 무작위로 뽑아 id 목록으로.

    per_item 이 있으면 항목마다 직접 정한 수, 없으면 객관식·주관식 수를 항목에 고르게 나눠 뽑음."""
    import random as _r
    pools = []
    for cat, sub in items:
        stmt = select(BankQuestion).where(BankQuestion.cat == cat, BankQuestion.review != "del")
        if sub:
            stmt = stmt.where(BankQuestion.subcat == sub)
        if levels:
            stmt = stmt.where(BankQuestion.level.in_(levels))
        if skip_unsure:
            stmt = stmt.where(BankQuestion.src != "확인 필요")
        rows = db.execute(stmt.order_by(func.random()).limit(300)).scalars().all()
        obj, sub_l = [], []
        for q in rows:
            d = display(q)
            if not with_passage and (d["passage"] or len(d["stem"]) > 300):
                continue
            (obj if d["objective"] else sub_l).append(q.id)
        pools.append({"obj": obj, "sub": sub_l})
    chosen: list[str] = []

    def take(kind, want_each):
        for i, pool in enumerate(pools):
            got = 0
            while pool[kind] and got < want_each[i]:
                chosen.append(pool[kind].pop())
                got += 1
            want_each[i] -= got

    def spread(total):
        k = len(pools) or 1
        return [total // k + (1 if i < total % k else 0) for i in range(len(pools))]

    if per_item:
        for i, pool in enumerate(pools):
            allq = pool["obj"] + pool["sub"]
            _r.shuffle(allq)
            chosen.extend(allq[: max(0, int(per_item.get(i, 0)))])
    else:
        for kind, total in (("obj", n_obj), ("sub", n_sub)):
            want = spread(total)
            take(kind, want)
            rest = sum(want)  # 모자란 항목 몫은 다른 항목에서 채움
            for pool in pools:
                while rest > 0 and pool[kind]:
                    chosen.append(pool[kind].pop())
                    rest -= 1
    return chosen


# ---------------------------------------------------------------- 문제 만들기 (학년 단계 → 중분류 → 소분류에서 고르기)

ESSAY_TYPES = {"서술형", "영작", "영작·쓰기", "문장 전환", "배열", "해석"}
KINDS = {"mc": "객관식", "short": "주관식", "essay": "서술형"}


def kind_of(q: BankQuestion, d: dict | None = None) -> str:
    """객관식(보기 고르기) / 서술형(영작·전환·배열·해석) / 주관식(단답·빈칸·오류수정 등)."""
    d = d or display(q)
    if d["objective"]:
        return "mc"
    return "essay" if (q.qtype or "") in ESSAY_TYPES else "short"


def tree(db: Session) -> list[dict]:
    """분류 체계 전체 + 소분류마다 낼 수 있는 문항 수 → [{id, label, cats: [{label, subs: [[sub_id, label, n]]}]}]"""
    from . import taxonomy

    counts = dict(db.execute(select(BankQuestion.sub_id, func.count()).where(BankQuestion.review != "del", BankQuestion.sub_id != "")
                             .group_by(BankQuestion.sub_id)).all())
    tx = taxonomy.load()
    out = {t: {"id": t, "label": tl, "cats": []} for t, tl in tx["tiers"]}
    for sid, s in tx["subs"].items():
        cats = out[s["tier"]]["cats"]
        if not cats or cats[-1]["label"] != s["cat"]:
            cats.append({"label": s["cat"], "subs": []})
        cats[-1]["subs"].append([sid, s["label"], counts.get(sid, 0)])
    return list(out.values())


def pick_by_subs(db: Session, sub_ids: list[str], per: int, kinds: list[str], levels: list[str],
                 skip_unsure: bool = True, with_passage: bool = True) -> list[str]:
    """소분류마다 per 문항씩, 켠 형식(kinds)·난이도(levels)에서 무작위로. 모자라면 있는 만큼."""
    import random as _r

    chosen: list[str] = []
    for sid in sub_ids:
        stmt = select(BankQuestion).where(BankQuestion.sub_id == sid, BankQuestion.review != "del")
        if levels:
            stmt = stmt.where(BankQuestion.level.in_(levels))
        if skip_unsure:
            stmt = stmt.where(BankQuestion.src != "확인 필요")
        rows = db.execute(stmt.order_by(func.random()).limit(max(60, per * 8))).scalars().all()
        pool = []
        for q in rows:
            d = display(q)
            if not with_passage and (d["passage"] or len(d["stem"]) > 300):
                continue
            if kinds and kind_of(q, d) not in kinds:
                continue
            pool.append(q.id)
        _r.shuffle(pool)
        chosen.extend(pool[:per])
    return chosen


# ---------------------------------------------------------------- 분류 재배치 (단원 묶음 → 학년 단계·소분류)

UNIT_KEY = ("book", "chapter", "unit", "cat", "subcat", "level")


def unit_groups(db: Session) -> list[dict]:
    """교재 단원(+ 기존 분류·난이도) 묶음마다 문항 수와 예시 문항 — 매핑 만들 때 쓰는 목록."""
    oc = func.coalesce(func.nullif(BankQuestion.old_cat, ""), BankQuestion.cat)
    osub = func.coalesce(func.nullif(BankQuestion.old_sub, ""), BankQuestion.subcat)
    cols = [BankQuestion.book, BankQuestion.chapter, BankQuestion.unit, oc, osub, BankQuestion.level]
    rows = db.execute(select(*cols, func.count(), func.min(BankQuestion.ord), func.min(BankQuestion.stem), func.max(BankQuestion.stem),
                             func.string_agg(func.distinct(BankQuestion.qtype), ","))
                      .where(BankQuestion.review != "del").group_by(*cols).order_by(func.min(BankQuestion.ord)))
    out = []
    for r in rows:
        d = dict(zip(UNIT_KEY, r[:6]))
        d.update({"n": r[6], "qtypes": r[10] or "", "ex": [(r[8] or "")[:140], (r[9] or "")[:140]] if r[8] != r[9] else [(r[8] or "")[:140]]})
        out.append(d)
    return out


def remap_rules(db: Session, step=None) -> dict:
    """bank/rules.py 규칙(교재 학년 단계 + 기존 분류 → 소분류)으로 전체 재배치."""
    from . import rules

    return remap(db, None, step, pick=lambda key: rules.choose(key[3], key[4], key[0], key[5]))


def remap(db: Session, mapping: list[dict] | None, step=None, pick=None) -> dict:
    """mapping: [{book, chapter, unit, cat, subcat, level, sub_id}] → 문항마다 tier·sub_id·cat(학년 단계 · 중분류)·subcat(소분류) 바꾸고
    분류 체계 순서대로 ord 다시 매김. 매핑에 없는 묶음은 cat 을 '미분류 · …'로."""
    from . import taxonomy

    subs = taxonomy.load()["subs"]
    by_key = {}
    for m in mapping or []:
        sid = str(m.get("sub_id") or "").strip()
        if sid in subs:
            by_key[tuple(str(m.get(k) or "") for k in UNIT_KEY)] = sid
    # 처음 재배치할 때 원래 분류를 old_cat/old_sub 에 보관 → 몇 번을 다시 돌려도 원래 분류 기준
    t0 = BankQuestion.__table__
    from sqlalchemy import update as _upd

    db.execute(_upd(t0).where(t0.c.old_cat == "").values(old_cat=t0.c.cat, old_sub=func.left(t0.c.subcat, 400)))
    rows = db.execute(select(BankQuestion.id, BankQuestion.book, BankQuestion.chapter, BankQuestion.unit, BankQuestion.old_cat,
                             BankQuestion.old_sub, BankQuestion.level, BankQuestion.ord)).all()
    lv = {"하": 0, "중": 1, "상": 2}
    upd, unmapped = [], 0
    for r in rows:
        key = tuple(str(x or "") for x in r[1:7])
        sid = pick(key) if pick else by_key.get(key)
        if sid:
            s = subs[sid]
            upd.append({"i": r[0], "tier": s["tier"], "sub_id": sid, "cat": f"{s['tier_label']} · {s['cat']}"[:60], "subcat": s["label"][:120],
                        "sort": (s["idx"], lv.get(r[6], 1), r[7] or 0)})
        else:
            unmapped += 1
            old = str(r[4] or "")
            upd.append({"i": r[0], "tier": "", "sub_id": "", "cat": f"미분류 · {old}"[:60], "subcat": str(r[5] or "")[:120],
                        "sort": (10**6, lv.get(r[6], 1), r[7] or 0)})
    upd.sort(key=lambda d: d["sort"])
    for n, d in enumerate(upd):
        d["o"] = n
        del d["sort"]
    t = BankQuestion.__table__
    from sqlalchemy import bindparam, update

    stmt = update(t).where(t.c.id == bindparam("i")).values(tier=bindparam("tier"), sub_id=bindparam("sub_id"), cat=bindparam("cat"),
                                                             subcat=bindparam("subcat"), ord=bindparam("o"))
    for k in range(0, len(upd), 2000):
        db.execute(stmt, upd[k:k + 2000])
        if step:
            step(k + 2000, len(upd))
    db.commit()
    return {"total": len(upd), "mapped": len(upd) - unmapped, "unmapped": unmapped}
