"""변형문제 (수능 유형) 규칙.

AI 없이 코드가 만드는 유형 (원문을 그대로 잘라서 → 정답이 틀릴 수 없음)
  order   순서 배열   첫 문장(들)을 주고 나머지를 (A)(B)(C) 세 덩어리로
  insert  문장 삽입   한 문장을 빼서 주고, 빈자리 후보 ①~⑤
AI가 만드는 유형 (지문마다 한 번 호출, 규칙 위반이면 그 유형만 다시 요청)
  topic 주제 · title 제목 · gist 요지 · blank 빈칸 추론 · irrelevant 무관한 문장
  summary 요약문 완성 · match 내용 일치 · vocab 어휘(문맥상 낱말)
"""
import random
import re

from ..analysis.spec import split_sentences
from ..grammar.spec import CIRCLED, place_marks

TYPES = {
    "topic": "주제",
    "title": "제목",
    "gist": "요지",
    "blank": "빈칸 추론",
    "order": "순서 배열",
    "insert": "문장 삽입",
    "irrelevant": "무관한 문장",
    "summary": "요약문 완성",
    "match": "내용 일치",
    "vocab": "어휘 (문맥상 낱말)",
}
CODE_TYPES = ["order", "insert"]
AI_TYPES = [t for t in TYPES if t not in CODE_TYPES]
DEFAULT_TYPES = ["topic", "blank", "order", "insert", "irrelevant", "summary"]
MAX_PASSAGES = 6

ORDER_CHOICES = ["(A)-(C)-(B)", "(B)-(A)-(C)", "(B)-(C)-(A)", "(C)-(A)-(B)", "(C)-(B)-(A)"]

STEMS = {
    "topic": "다음 글의 주제로 가장 적절한 것은?",
    "title": "다음 글의 제목으로 가장 적절한 것은?",
    "gist": "다음 글의 요지로 가장 적절한 것은?",
    "blank": "다음 빈칸에 들어갈 말로 가장 적절한 것은?",
    "order": "주어진 글 다음에 이어질 글의 순서로 가장 적절한 것은?",
    "insert": "글의 흐름으로 보아, 주어진 문장이 들어가기에 가장 적절한 곳은?",
    "irrelevant": "다음 글에서 전체 흐름과 관계 없는 문장은?",
    "summary": "다음 글의 내용을 한 문장으로 요약하고자 한다. 빈칸 (A), (B)에 들어갈 말로 가장 적절한 것은?",
    "match_wrong": "다음 글의 내용과 일치하지 않는 것은?",
    "match_right": "다음 글의 내용과 일치하는 것은?",
    "vocab": "다음 글의 밑줄 친 부분 중, 문맥상 낱말의 쓰임이 적절하지 않은 것은?",
}

MIN_SENTENCES = {"order": 4, "insert": 6, "irrelevant": 5}
_LINK = re.compile(r"^(However|But|Yet|Still|Instead|Therefore|Thus|Hence|As a result|For example|For instance|In contrast|On the other hand|"
                   r"Moreover|Furthermore|In addition|This|These|That|Those|Such|It|They|He|She|Nevertheless|Otherwise|Similarly|Likewise)\b")


def _s(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _idx(v) -> int:
    t = str(v or "").strip()
    if t[:1] in CIRCLED:
        return CIRCLED.index(t[0]) + 1
    m = re.search(r"\d+", t)
    i = int(m.group()) if m else 0
    return i if 1 <= i <= 5 else 0


# ---------------------------------------------------------------- 코드로 만드는 유형

def make_order(passage: str, seed: int) -> dict | None:
    s = split_sentences(passage)
    if len(s) < MIN_SENTENCES["order"]:
        return None
    n_intro = 2 if len(s) >= 9 else 1
    intro, rest = s[:n_intro], s[n_intro:]
    k = len(rest)
    cuts = [round(k / 3), round(2 * k / 3)]
    chunks = [rest[: cuts[0]], rest[cuts[0]: cuts[1]], rest[cuts[1]:]]
    if any(not c for c in chunks):
        return None
    rng = random.Random(seed)
    answer = rng.randint(1, 5)
    labels_in_order = re.findall(r"[ABC]", ORDER_CHOICES[answer - 1])  # 원래 순서대로 붙일 이름
    parts = {lab: " ".join(chunks[i]) for i, lab in enumerate(labels_in_order)}
    return {"type": "order", "intro": " ".join(intro), "parts": {k: parts[k] for k in "ABC"}, "answer": answer,
            "explain": f"원래 순서는 {ORDER_CHOICES[answer - 1]}입니다."}


def make_insert(passage: str, seed: int) -> dict | None:
    s = split_sentences(passage)
    if len(s) < MIN_SENTENCES["insert"]:
        return None
    rng = random.Random(seed)
    cands = [i for i in range(2, len(s)) if _LINK.match(s[i])] or list(range(2, len(s)))
    t = rng.choice(cands)
    given, rest = s[t], s[:t] + s[t + 1:]
    true_gap = t - 1  # rest[t-1] 다음 자리
    boundaries = list(range(0, len(rest)))  # gap g = rest[g] 다음 (마지막 문장 뒤 포함)
    start = max(0, min(true_gap - rng.randint(0, 4), len(boundaries) - 5))
    gaps = boundaries[start:start + 5]
    if true_gap not in gaps or len(gaps) < 5:
        return None
    return {"type": "insert", "given": given, "sentences": rest, "gaps": gaps, "answer": gaps.index(true_gap) + 1,
            "explain": "주어진 문장 앞뒤의 연결어·지시어가 가리키는 내용을 확인합니다."}


def make_irrelevant(passage: str, extra: str, seed: int) -> dict | None:
    """첫 문장은 번호 없이, 이어지는 네 문장 사이에 AI가 쓴 무관한 문장을 끼워 ①~⑤."""
    s = split_sentences(passage)
    if len(s) < MIN_SENTENCES["irrelevant"] or not extra:
        return None
    window, tail = s[1:5], s[5:]
    pos = random.Random(seed).randint(1, 4)  # 맨 앞(①)은 피해서 끼움
    numbered = window[:pos] + [extra] + window[pos:]
    return {"type": "irrelevant", "lead": s[0], "numbered": numbered, "tail": " ".join(tail), "answer": pos + 1}


# ---------------------------------------------------------------- AI 결과 정리 · 검증

def clean_ai(t: str, raw: dict, passage: str, seed: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    explain = _s(raw.get("explain"))
    if t in ("topic", "title", "gist"):
        return {"type": t, "choices": [_s(c) for c in (raw.get("choices") or [])][:5], "answer": _idx(raw.get("answer")), "explain": explain}
    if t == "blank":
        phrase = _s(raw.get("phrase"))
        m = re.search(r"(?<![A-Za-z0-9'])" + re.escape(phrase) + r"(?![A-Za-z0-9'])", passage) if phrase else None
        return {"type": t, "phrase": phrase, "before": passage[: m.start()] if m else "", "after": passage[m.end():] if m else "",
                "found": bool(m), "choices": [_s(c) for c in (raw.get("choices") or [])][:5], "answer": _idx(raw.get("answer")), "explain": explain}
    if t == "irrelevant":
        q = make_irrelevant(passage, _s(raw.get("sentence")), seed)
        if q:
            q["explain"] = explain
        return q or {"type": t, "numbered": [], "answer": 0, "explain": explain}
    if t == "summary":
        pairs = []
        for c in (raw.get("choices") or [])[:5]:
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                pairs.append([_s(c[0]), _s(c[1])])
            elif isinstance(c, dict):
                pairs.append([_s(c.get("A")), _s(c.get("B"))])
        return {"type": t, "summary": _s(raw.get("summary")), "choices": pairs, "answer": _idx(raw.get("answer")), "explain": explain}
    if t == "match":
        ask = "right" if str(raw.get("ask", "")).lower().startswith("r") else "wrong"
        return {"type": t, "ask": ask, "choices": [_s(c) for c in (raw.get("choices") or [])][:5], "answer": _idx(raw.get("answer")), "explain": explain}
    if t == "vocab":
        marks = [{"text": str(m.get("text", "")).strip(), "wrong": bool(m.get("wrong")), "fix": _s(m.get("fix")), "why": _s(m.get("why"))}
                 for m in (raw.get("marks") or []) if isinstance(m, dict)]
        return {"type": t, "passage": (raw.get("passage") or "").strip(), "marks": marks, "explain": explain}
    return None


def validate(q: dict | None) -> list[str]:
    if not q:
        return ["형식이 맞지 않습니다."]
    t, name = q["type"], TYPES.get(q["type"], q["type"])
    errs = []
    if t in ("topic", "title", "gist", "blank", "match", "summary"):
        if len(q.get("choices") or []) != 5 or any(not c or (isinstance(c, list) and not all(c)) for c in q["choices"]):
            errs.append(f"{name}: 보기가 5개여야 합니다.")
        if not q.get("answer"):
            errs.append(f"{name}: 정답 번호(1~5)가 없습니다.")
    if t == "blank" and not q.get("found"):
        errs.append("빈칸 추론: 빈칸으로 만들 구절(phrase)을 지문에서 글자 그대로 찾지 못했습니다.")
    if t == "summary" and ("(A)" not in q.get("summary", "") or "(B)" not in q.get("summary", "")):
        errs.append("요약문 완성: 요약문에 (A)와 (B) 빈칸이 모두 있어야 합니다.")
    if t == "irrelevant" and len(q.get("numbered") or []) != 5:
        errs.append("무관한 문장: 지문이 짧거나 끼워 넣을 문장(sentence)이 없습니다.")
    if t == "order" and set(q.get("parts", {})) != set("ABC"):
        errs.append("순서 배열: 덩어리가 3개가 아닙니다.")
    if t == "insert" and len(q.get("gaps") or []) != 5:
        errs.append("문장 삽입: 빈자리 후보가 5개가 아닙니다.")
    if t == "vocab":
        marks = q.get("marks") or []
        if len(marks) != 5:
            errs.append(f"어휘: 밑줄이 5개여야 하는데 {len(marks)}개입니다.")
        elif place_marks(q.get("passage", ""), marks) is None:
            errs.append("어휘: 밑줄 낱말을 지문에서 순서대로 찾지 못했습니다.")
        if sum(m["wrong"] for m in marks) != 1:
            errs.append("어휘: 쓰임이 틀린 밑줄은 정확히 1개여야 합니다.")
    return errs


def answer_text(q: dict) -> str:
    a = q.get("answer") or 0
    if q["type"] == "vocab":
        wrong = [i for i, m in enumerate(q["marks"]) if m["wrong"]]
        return CIRCLED[wrong[0]] if wrong else "?"
    return CIRCLED[a - 1] if 1 <= a <= 5 else "?"


# ---------------------------------------------------------------- 프롬프트

SYSTEM_PROMPT = """당신은 한국 수능·내신 영어 독해 문제를 출제하는 교사입니다. JSON 객체 하나로만 답합니다.
- 정답은 반드시 하나. 오답 보기는 지문 어휘를 일부 쓰되 핵심이 어긋나게, 그럴듯하게 만듭니다.
- 보기 순서는 정답 위치가 한쪽으로 몰리지 않게 섞습니다.
- explain: 한국어 1~2문장, 정답 근거를 지문에서 짚습니다."""

FORMATS = {
    "topic": '"topic": {"choices": ["영어 보기 5개"], "answer": 1~5, "explain": "..."}  // 주제: 영어 명사구 보기',
    "title": '"title": {"choices": ["영어 제목 5개"], "answer": 1~5, "explain": "..."}  // 제목: 영어 제목 보기',
    "gist": '"gist": {"choices": ["한국어 요지 5개"], "answer": 1~5, "explain": "..."}  // 요지: 한국어 문장 보기',
    "blank": '"blank": {"phrase": "지문 속 핵심 구절(3~10단어, 글자 그대로)", "choices": ["영어 보기 5개"], "answer": 1~5, "explain": "..."}  // 정답 보기는 phrase와 같거나 같은 뜻',
    "irrelevant": '"irrelevant": {"sentence": "지문 소재와 관련 있어 보이지만 글의 흐름과 무관한 영어 문장 하나", "explain": "..."}',
    "summary": '"summary": {"summary": "글 전체를 요약한 영어 한 문장, 빈칸 두 곳을 (A), (B)로", "choices": [["A보기","B보기"] 5쌍], "answer": 1~5, "explain": "..."}',
    "match": '"match": {"ask": "wrong" 또는 "right", "choices": ["한국어 진술 5개, 지문 순서대로"], "answer": 1~5, "explain": "..."}  // wrong이면 불일치가 정확히 1개',
    "vocab": '"vocab": {"passage": "낱말 하나를 문맥과 반대·부적절한 낱말로 바꾼 지문 전체", "marks": [{"text": "밑줄 낱말(지문 글자 그대로, 1~2단어)", "wrong": false, "fix": "", "why": "..."}, ... 정확히 5개, 앞에서부터 순서대로, 틀린 것 정확히 1개(wrong: true, fix: 원래 알맞은 낱말)], "explain": "..."}',
}


def ai_prompt(passage: str, types: list[str], level: str, fix_notes: list[str] | None = None) -> str:
    lines = [f"대상: {level or '고등학생'}", "아래 지문으로 다음 유형의 문제를 하나씩 만드세요. 출력은 유형 이름을 키로 하는 JSON 객체입니다.", "{"]
    lines += ["  " + FORMATS[t] + "," for t in types]
    lines.append("}")
    if fix_notes:
        lines.append("\n직전 답의 문제점 (이 유형들은 규칙에 맞게 다시):\n- " + "\n- ".join(fix_notes))
    lines.append("\n지문:\n" + passage.strip())
    return "\n".join(lines)
