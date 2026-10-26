"""동형 모의고사 규칙.

흐름
  1. 설계도(slots): 학교 기출 한 회(기출 분석 결과)의 문항을 그대로 옮김 — 번호·배점·유형·발문·지문 묶음.
     지문을 같이 쓰는 문항끼리 한 '묶음(unit)'. 긴 지문이 있는 묶음 = 지문 묶음, 없으면 = 단독 문항(어휘·문장 단위 문제).
  2. 배정(plan): 지문 묶음마다 새 범위 지문 하나 (Gemini가 유형에 맞게 고르고, 안 되면 넣은 순서대로).
  3. 출제: 묶음마다 Gemini 한 번 — 원래 문항이 지문을 바꾼 방식(밑줄 ①~⑤, 빈칸, 문장 끼우기…)과 발문·보기 형식을 그대로 따라 새 문항.
     단독 문항은 범위 지문의 어휘로 몇 개씩 한꺼번에.
  4. 검사 → 규칙 위반이면 그 묶음만 한 번 더. 정답 번호는 코드가 고르게 섞음(섞어도 되는 문항만).
글 표시: 밑줄 [u]…[/u], 빈칸 _____ (밑줄 5개 이상), 줄바꿈 \n
"""
import html
import random
import re

from markupsafe import Markup

CIRCLED = "①②③④⑤"
MIN_PASSAGE_WORDS = 40
MAX_PASSAGES = 30
STANDALONE_BATCH = 6


def _s(v) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", re.sub(r"\[/?u\]", "", text or "")))


def is_sa(label: str, kind: str = "") -> bool:
    return kind == "sa" or bool(re.search(r"서[술답]|주관|논술", label or ""))


# ---------------------------------------------------------------- 1. 설계도

def blueprint(questions: list[dict]) -> list[dict]:
    """기출 분석 문항 → 설계도 칸. unit: 지문을 같이 쓰는 칸끼리 같은 번호."""
    slots = []
    unit_of: dict[str, int] = {}
    n_unit = 0
    for q in questions:
        lead = q.get("shared_with") or ""
        if lead and lead in unit_of:
            u = unit_of[lead]
        else:
            u = n_unit
            n_unit += 1
        unit_of[q["label"]] = u
        slots.append({
            "label": q["label"], "kind": q.get("kind", "mc"), "points": q.get("points"), "type": q.get("type", ""),
            "stem": q.get("stem", ""), "group": q.get("group", ""), "unit": u,
            "passage_words": words(q.get("passage", "")),
            "example": {k: q.get(k) for k in ("stem", "passage", "box", "choices", "conditions", "point") if q.get(k)},
            "p": None,
        })
    # 묶음 안 어느 칸에든 긴 지문이 있으면 지문 묶음
    long_units = {s["unit"] for s in slots if s["passage_words"] >= MIN_PASSAGE_WORDS}
    for s in slots:
        s["needs_passage"] = s["unit"] in long_units
    return slots


def plain(text: str) -> str:
    """시험지에 실린 지문 → 표시를 걷어낸 글 (밑줄 [u], 지문 속 ①~⑤·(a)~(e) 번호 표시)."""
    t = re.sub(r"\[/?u\]", "", text or "")
    t = re.sub(r"\(\s*[①②③④⑤]\s*\)|[①②③④⑤ⓐⓑⓒⓓⓔ]\s*", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def same_passages(slots: list[dict], questions: list[dict]) -> tuple[list[dict], list[dict]]:
    """'같은 지문으로' 모드: 지문 묶음마다 원래 시험지의 그 지문을 그대로 배정."""
    by_label = {q["label"]: q for q in questions}
    passages, unit_p = [], {}
    for u in units(slots):
        if not u[0]["needs_passage"]:
            continue
        lead = max(u, key=lambda s: s["passage_words"])
        q = by_label.get(lead["label"], {})
        unit_p[u[0]["unit"]] = len(passages)
        src = f"원래 시험 {lead['label']}{'번' if lead['label'].isdigit() else ''} 지문"
        passages.append({"title": src, "source": src, "text": plain(q.get("passage", "")), "same": True})
    for s in slots:
        s["p"] = unit_p.get(s["unit"]) if s["needs_passage"] else None
    return slots, passages


SAME_RULE = """이번에는 '같은 지문 다른 문제'입니다. 새 지문은 원래 문항의 지문과 같은 글입니다 (표시를 걷어낸 상태).
- 같은 유형·발문·배점·형식을 지키되, 문제 내용은 원래 문항과 반드시 다르게: 정답 번호와 정답 내용, 밑줄·빈칸·①~⑤ 위치,
  바꿔 넣은 낱말, 무관한 문장, 어법으로 묻는 구문, 서술형 정답 문장이 모두 원래 문항과 겹치지 않게 합니다.
- 원래 문항에서 바꿔 실은 부분(반의어로 바꾼 낱말, 끼워 넣은 문장, 빈칸)은 원문으로 되돌린 뒤 새로 출제합니다. 원문을 알 수 없으면 문맥상 자연스러운 말로.
- change 에는 원래 문항과 무엇을 다르게 냈는지 한 줄로."""


def units(slots: list[dict]) -> list[list[dict]]:
    out: dict[int, list[dict]] = {}
    for s in slots:
        out.setdefault(s["unit"], []).append(s)
    return [out[k] for k in sorted(out)]


def default_plan(slots: list[dict], n_passages: int) -> None:
    """넣은 순서대로 지문 묶음에 지문을 하나씩 (모자라면 처음부터 다시)."""
    pu = [u for u in units(slots) if u[0]["needs_passage"]]
    for i, u in enumerate(pu):
        for s in u:
            s["p"] = i % n_passages if n_passages else None


def apply_plan(slots: list[dict], raw, n_passages: int) -> bool:
    """Gemini 배정 {"assign": [{"unit": 3, "passage": 1}, ...]} 적용. 지문 묶음이 다 채워지면 True."""
    if not isinstance(raw, dict):
        return False
    got = {}
    for a in raw.get("assign") or []:
        if not isinstance(a, dict):
            continue
        try:
            u, p = int(a.get("unit")), int(a.get("passage"))
        except (TypeError, ValueError):
            continue
        if 0 <= p < n_passages:
            got[u] = p
    need = {s["unit"] for s in slots if s["needs_passage"]}
    if not need or not need <= set(got):
        return False
    for s in slots:
        s["p"] = got.get(s["unit"]) if s["needs_passage"] else None
    return True


# ---------------------------------------------------------------- 2. 프롬프트

SYSTEM = """당신은 한국 고등학교 영어 내신 시험을 출제하는 교사입니다. 특정 학교의 지난 시험 문항을 보고, 새 시험 범위 지문으로 '같은 형식'의 문항을 만듭니다.
- 발문·배점·보기 개수·<보기>/[조건] 상자·서술형 조건의 모양을 원래 문항과 똑같이 맞춥니다.
- 원래 문항이 지문을 바꾼 방식(밑줄 ①~⑤, 빈칸, 낱말 바꾸기, 무관한 문장 끼우기, (A)(B) 표시, 일부만 싣기 등)을 새 지문에 그대로 적용합니다.
- 새 지문의 문장·단어는 문항이 요구하는 변형 말고는 원문 그대로 씁니다. 철자·문장부호도 그대로.
- 객관식은 정답이 정확히 하나. 오답은 그럴듯하게. 영어 문장은 원어민 기준으로 자연스럽고 정확하게.
- 표시: 밑줄은 [u]…[/u], 빈칸은 _____ , 줄바꿈은 \\n. ①~⑤ (A) (a) ㄱ 같은 기호는 글자 그대로.
- 해설(explain)·채점 기준(grading)·바꾼 점(change)은 쉬운 한국어로.
JSON 객체 하나로만 답합니다."""

ITEM_FORMAT = """{"label": "문항 번호(설계도 그대로)",
 "stem": "발문",
 "passage": "이 문항에 실을 지문/문장 (지문을 같이 쓰는 묶음이면 첫 문항에만, 나머지는 빈 문자열)",
 "box": "<보기>·주어진 문장·요약문·주어진 단어 등 상자 내용 (없으면 빈 문자열)",
 "conditions": "서술형 [조건] (줄마다 \\n, 객관식은 빈 문자열)",
 "choices": ["객관식 보기 5개 (기호 없이 내용만). 보기가 지문 속 ①~⑤ 자체면 [\\"①\\",\\"②\\",\\"③\\",\\"④\\",\\"⑤\\"]. 서술형은 []"],
 "answer": "객관식 정답 번호 1~5 (서술형은 0)",
 "answer_text": "서술형 모범 답안 (객관식은 빈 문자열)",
 "grading": "서술형 채점 기준·부분 점수 (객관식은 빈 문자열)",
 "explain": "정답 근거 해설 1~2문장",
 "point": "출제 포인트 한 줄 (예: 분사구문 능동/수동, 반의어 치환, 요지 파악)",
 "fixed": "보기 순서를 섞으면 안 되면 true (보기가 ①~⑤·(a)~(e)·ㄱㄴㄷ 조합·순서 배열처럼 위치를 가리킬 때)"}"""


def _example(s: dict, cut: int = 1400) -> dict:
    ex = dict(s["example"])
    if ex.get("passage") and len(ex["passage"]) > cut:
        ex["passage"] = ex["passage"][:cut] + " …(이하 생략)"
    return {"label": s["label"], "kind": "서술형" if s["kind"] == "sa" else "객관식", "points": s["points"], "type": s["type"], **ex}


def plan_prompt(slots: list[dict], passages: list[dict]) -> str:
    import json

    us = []
    for u in units(slots):
        if not u[0]["needs_passage"]:
            continue
        us.append({"unit": u[0]["unit"], "labels": [s["label"] for s in u], "types": [s["type"] for s in u],
                   "stems": [s["stem"][:60] for s in u], "original_words": max(s["passage_words"] for s in u)})
    ps = [{"passage": i, "source": p.get("source") or p.get("title") or "", "words": words(p["text"]),
           "start": " ".join(p["text"].split()[:30])} for i, p in enumerate(passages)]
    return ("학교 시험 설계도의 '지문 묶음'마다 새 시험 범위 지문을 하나씩 정해 주세요.\n"
            "- 유형에 어울리는 지문으로 (심경·일화는 이야기 글, 순서·삽입·무관한 문장은 문장이 많은 논리적인 글, 서술형 영작은 쓸 만한 구문이 있는 글).\n"
            "- 원래 지문 길이(original_words)와 비슷한 지문이면 더 좋습니다.\n"
            "- 모든 지문을 최소 한 번씩 쓰고, 같은 지문은 지문 묶음 수가 지문 수보다 많을 때만 두 번 씁니다.\n"
            "- 교과서 지문과 모의고사 지문이 원래 시험처럼 고르게 섞이게.\n\n"
            f"지문 묶음:\n{json.dumps(us, ensure_ascii=False)}\n\n새 지문:\n{json.dumps(ps, ensure_ascii=False)}\n\n"
            '출력: {"assign": [{"unit": 묶음 번호, "passage": 지문 번호}, ...]}')


def unit_prompt(unit: list[dict], passage: dict, memo_text: str = "", fix: list[str] | None = None, level: str = "") -> str:
    import json

    lines = []
    if memo_text:
        lines += ["이 학교 출제 스타일 메모:", memo_text, ""]
    if passage.get("same"):
        lines += [SAME_RULE, ""]
    lines += [f"대상: {level or '고등학생'}",
              "아래 '원래 문항'들은 이 학교 지난 시험에서 한 지문을 같이 쓴 문항 묶음입니다. 새 지문으로 같은 형식의 문항을 같은 번호·배점으로 만들어 주세요.",
              "원래 문항:", json.dumps([_example(s) for s in unit], ensure_ascii=False, indent=1), "",
              f"새 지문 (출처: {passage.get('source') or passage.get('title') or '시험 범위'}):", passage["text"].strip(), "",
              "출력: {\"change\": \"새 지문을 원문에서 어떻게 바꿔 실었는지 한 줄\", \"items\": [문항마다 아래 형식]}", ITEM_FORMAT]
    if fix:
        lines += ["", "직전 답의 문제점 (고쳐서 다시):", *[f"- {x}" for x in fix]]
    return "\n".join(lines)


def standalone_prompt(batch: list[dict], passages: list[dict], used_words: list[str], memo_text: str = "", fix: list[str] | None = None, level: str = "") -> str:
    import json

    pool = vocab_pool(passages)
    lines = []
    if memo_text:
        lines += ["이 학교 출제 스타일 메모:", memo_text, ""]
    lines += [f"대상: {level or '고등학생'}",
              "아래 '원래 문항'들은 이 학교 지난 시험의 지문 없는 문항(어휘·문장 단위)입니다. 새 시험 범위의 어휘·문장으로 같은 형식의 문항을 같은 번호·배점으로 만들어 주세요.",
              "- 정답 어휘는 새 범위 지문에 나온 단어로. 원래 문항의 오답 방식(같은 어원·접두사, 철자 비슷한 말, 다의어 등)을 그대로 따릅니다.",
              *(["- 이번 범위는 원래 시험과 같은 지문입니다. 원래 문항과 다른 정답 어휘·다른 예문으로 내 주세요."] if any(p.get("same") for p in passages) else []),
              "- 문장(예문)은 새 범위 지문의 문장을 쓰거나 살짝 줄여서. 원래 문항에 한글 해석이 있었으면 passage 끝에 \\n(한글 해석)으로.",
              f"- 이미 다른 문항에서 쓴 정답 어휘(겹치지 않게): {', '.join(used_words) or '없음'}",
              "원래 문항:", json.dumps([_example(s, 600) for s in batch], ensure_ascii=False, indent=1), "",
              "새 범위 어휘 후보 (지문에서 뽑음):", ", ".join(pool), "",
              "새 범위 지문 (예문을 가져올 곳):"]
    for i, p in enumerate(passages):
        lines.append(f"[{i + 1}] {p.get('source') or p.get('title') or ''}: {p['text'][:1500]}")
    lines += ["", "출력: {\"items\": [문항마다 아래 형식]}", ITEM_FORMAT]
    if fix:
        lines += ["", "직전 답의 문제점 (고쳐서 다시):", *[f"- {x}" for x in fix]]
    return "\n".join(lines)


STOP = set("""about above after again against because before being below between could doesn't during further having himself herself itself
themselves through under until which while would should there their these those where other another every people something
however without within because really things thinking""".split())


def vocab_pool(passages: list[dict], n: int = 120) -> list[str]:
    seen, out = set(), []
    for p in passages:
        for w in re.findall(r"[A-Za-z][a-z]{5,}", p["text"]):
            lw = w.lower()
            if lw in STOP or lw in seen:
                continue
            seen.add(lw)
            out.append(lw)
    return out[:n]


# ---------------------------------------------------------------- 3. 정리 · 검사

def _idx(v) -> int:
    t = str(v or "").strip()
    if t[:1] in CIRCLED:
        return CIRCLED.index(t[0]) + 1
    m = re.search(r"\d+", t)
    i = int(m.group()) if m else 0
    return i if 1 <= i <= 5 else 0


def _choice(c) -> str:
    c = _s(c)
    if len(c) > 1:
        c = re.sub(r"^[①②③④⑤]\s*", "", c)
    return c


def clean_item(raw: dict, slot: dict, src: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    sa = slot["kind"] == "sa"
    choices = [] if sa else [_choice(c) for c in (raw.get("choices") or [])][:5]
    fixed = str(raw.get("fixed", "")).lower() in ("true", "1", "yes")
    return {
        "label": slot["label"], "kind": slot["kind"], "points": slot["points"], "type": slot["type"], "unit": slot["unit"],
        "stem": _s(raw.get("stem")) or slot["stem"],
        "passage": str(raw.get("passage") or "").strip(), "box": str(raw.get("box") or "").strip(),
        "conditions": str(raw.get("conditions") or "").strip(),
        "choices": choices, "answer": 0 if sa else _idx(raw.get("answer")),
        "answer_text": _s(raw.get("answer_text")), "grading": _s(raw.get("grading")),
        "explain": _s(raw.get("explain")), "point": _s(raw.get("point"))[:120],
        "fixed": fixed or must_fix(slot, choices, raw), "src": src,
    }


_POS = re.compile(r"^(\(?[a-eA-E]\)?|[ㄱ-ㅎ](\s*,\s*[ㄱ-ㅎ])*|\(?[A-C]\)?\s*[-–]\s*\(?[A-C]\)?.*|[①②③④⑤]|[ⓐⓑⓒⓓⓔ])$")


def must_fix(slot: dict, choices: list[str], raw: dict | None = None) -> bool:
    """보기 순서를 섞으면 안 되는 문항."""
    if slot["type"] in ("순서", "삽입", "무관한 문장"):
        return True
    st = slot.get("stem", "") + " " + _s((raw or {}).get("stem"))
    if re.search(r"밑줄\s*친\s*(부분|낱말|단어)?\s*중|[①⑤]|\(a\)\s*[~∼-]\s*\(e\)|ⓐ\s*[~∼-]\s*ⓔ|<보기>.*고른|옳은\s*것만|있는\s*대로", st):
        return True
    return bool(choices) and all(_POS.match(c.replace(" ", "")) or _POS.match(c) for c in choices)


def needs_marks(item: dict) -> bool:
    st = item["stem"]
    return item["kind"] == "mc" and (item["type"] in ("무관한 문장", "삽입") or bool(re.search(r"밑줄\s*친\s*(부분|낱말|단어)?\s*중", st)))


def validate(item: dict, has_passage: bool) -> list[str]:
    lab = item["label"] + ("번" if item["label"].isdigit() else "")
    errs = []
    if not item["stem"]:
        errs.append(f"{lab}: 발문이 비었습니다.")
    if item["kind"] == "mc":
        if len(item["choices"]) != 5 or any(not c for c in item["choices"]):
            errs.append(f"{lab}: 객관식 보기가 5개여야 합니다.")
        if not item["answer"]:
            errs.append(f"{lab}: 정답 번호(1~5)가 없습니다.")
    else:
        if not item["answer_text"]:
            errs.append(f"{lab}: 서술형 모범 답안(answer_text)이 없습니다.")
    text = item["passage"] + "\n" + item["box"]
    if has_passage and needs_marks(item):
        missing = [c for c in CIRCLED if c not in text]
        if missing:
            errs.append(f"{lab}: 지문에 {''.join(missing)} 표시가 없습니다 (발문이 지문 속 ①~⑤를 가리킴).")
    if item["kind"] == "mc" and item["type"] == "빈칸" and "____" not in text and "빈칸" in item["stem"]:
        errs.append(f"{lab}: 빈칸 문제인데 지문에 빈칸(_____)이 없습니다.")
    return errs


def validate_unit(items: list[dict], unit: list[dict]) -> list[str]:
    errs = []
    labels = [s["label"] for s in unit]
    got = [it["label"] for it in items]
    for lab in labels:
        if lab not in got:
            errs.append(f"{lab}번 문항이 빠졌습니다.")
    has_passage = any(it["passage"] for it in items) or not unit[0]["needs_passage"]
    if unit[0]["needs_passage"] and not any(words(it["passage"]) >= 30 for it in items):
        errs.append("지문 묶음인데 지문(passage)이 비었습니다. 첫 문항 passage에 지문 전체를 넣어 주세요.")
    for it in items:
        errs += validate(it, has_passage)
    return errs


def balance(items: list[dict], seed: int = 0) -> None:
    """섞어도 되는 객관식만 정답 번호가 ①~⑤에 고르게 가도록 보기 순서를 바꿈."""
    mc = [it for it in items if it["kind"] == "mc" and len(it["choices"]) == 5 and it["answer"]]
    movable = [it for it in mc if not it.get("fixed")]
    counts = {k: 0 for k in range(1, 6)}
    for it in mc:
        if it.get("fixed"):
            counts[it["answer"]] += 1
    total = len(mc)
    target = {k: total // 5 + (1 if k > 5 - total % 5 else 0) for k in range(1, 6)}
    pool = []
    for k in range(1, 6):
        pool += [k] * max(0, target[k] - counts[k])
    rnd = random.Random(seed)
    while len(pool) < len(movable):
        pool.append(rnd.randint(1, 5))
    rnd.shuffle(pool)
    for it, a in zip(movable, pool):
        right = it["choices"][it["answer"] - 1]
        rest = [c for i, c in enumerate(it["choices"]) if i != it["answer"] - 1]
        rnd.shuffle(rest)
        rest.insert(a - 1, right)
        it["choices"], it["answer"] = rest, a


def order_items(items: list[dict], slots: list[dict]) -> list[dict]:
    pos = {s["label"]: i for i, s in enumerate(slots)}
    return sorted(items, key=lambda it: pos.get(it["label"], 999))


# ---------------------------------------------------------------- 4. 표시

def rich(text: str) -> Markup:
    t = html.escape(text or "")
    t = t.replace("[u]", "<u>").replace("[/u]", "</u>")
    t = re.sub(r"_{4,}", '<span class="bl"></span>', t)
    return Markup(t.replace("\n", "<br>"))


def lab(label: str) -> str:
    return f"{label}." if label.isdigit() else label


def counts(items: list[dict]) -> dict:
    mc = [i for i in items if i["kind"] == "mc"]
    sa = [i for i in items if i["kind"] == "sa"]
    short = [i for i in sa if "단답" in (i["label"] + i["stem"])]
    f = lambda xs: round(sum(x["points"] or 0 for x in xs), 2)
    long = [i for i in sa if i not in short]
    return {"mc": len(mc), "mc_pts": f(mc), "sa": len(sa), "sa_pts": f(sa), "n": len(items), "total": f(items),
            "short": len(short), "short_pts": f(short), "long": len(long), "long_pts": f(long)}


def fmt(x) -> str:
    if x is None:
        return "-"
    return f"{round(float(x), 2):.2f}".rstrip("0").rstrip(".")


def level(points, all_points: list[float]) -> str:
    """배점 기준 난이도 (학교 표처럼: 그 시험 배점 분포의 아래 1/3 하, 위 1/3 상)."""
    if points is None or not all_points:
        return ""
    xs = sorted(all_points)
    lo, hi = xs[len(xs) // 3], xs[(2 * len(xs)) // 3]
    return "하" if points < lo else ("상" if points > hi else "중")
