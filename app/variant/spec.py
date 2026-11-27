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
    "blank_word": "빈칸(단어)",
    "blank": "빈칸(구)",
    "blank_sent": "빈칸(문장)",
    "vocab": "어휘",
    "grammar": "어법",
    "order": "순서",
    "insert": "문장삽입",
    "implied": "함축의미",
    "irrelevant": "무관한 문장",
    "summary": "요약문",
    "def_pick": "영영풀이",
    "match_en_r": "내용일치(영)",
    "match_ko_r": "내용일치(한)",
    "match_en_w": "내용불일치(영)",
    "match_ko_w": "내용불일치(한)",
    "purpose": "목적",
    "mood": "심경",
    "refer": "지칭 추론",
    "gram_explain": "어법 분석",
    "right_sent": "옳은 문장 찾기",
    "definition": "영영풀이 오류 찾기",
    "link2": "연결어 2빈칸 추론",
    "sa_grammar": "서술형 · 어법 고쳐 쓰기",
    "sa_arrange": "서술형 · 배열 영작",
    "sa_summary": "서술형 · 요약문 완성",
    "match": "내용 일치(예전)",
}
LEGACY_TYPES = ("match",)  # 예전에 만든 문항만 (새로 고를 수 없음)
SA_TYPES = ("sa_grammar", "sa_arrange", "sa_summary")
MC_TYPES = [t for t in TYPES if t not in SA_TYPES and t not in LEGACY_TYPES]
MARK_TYPES = ("vocab", "grammar", "sa_grammar")  # 지문에 밑줄 5개, 틀린 것 표시
CODE_TYPES = ["order", "insert"]
AI_TYPES = [t for t in TYPES if t not in CODE_TYPES]
DEFAULT_TYPES = ["topic", "blank", "order", "insert", "irrelevant", "summary"]
CHOICE_TYPES = ("topic", "title", "gist", "blank", "match", "blank_word", "blank_sent", "implied", "def_pick",
                "match_en_r", "match_ko_r", "match_en_w", "match_ko_w", "purpose", "mood", "right_sent")  # 보기 5개 + 정답 번호
MATCH4 = {"match_en_r": "right", "match_ko_r": "right", "match_en_w": "wrong", "match_ko_w": "wrong"}
NEW_VIEW_TYPES = ("blank_word", "blank_sent", "implied", "def_pick", "match_en_r", "match_ko_r", "match_en_w", "match_ko_w",
                  "purpose", "mood", "refer", "gram_explain", "right_sent", "link2")
MAX_PER_TYPE = 60
LETTERS = "ⓐⓑⓒⓓⓔ"
MAX_PASSAGES = 60

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
    "grammar": "다음 글의 밑줄 친 부분 중, 어법상 틀린 것은?",
    "definition": "다음 글의 밑줄 친 ⓐ~ⓔ의 영영풀이로 적절하지 않은 것은?",
    "blank_word": "다음 빈칸에 들어갈 말로 가장 적절한 것은?",
    "blank_sent": "다음 빈칸에 들어갈 말로 가장 적절한 것은?",
    "implied": "밑줄 친 부분이 다음 글에서 의미하는 바로 가장 적절한 것은?",
    "def_pick": "다음 글에 쓰인 낱말 중, 주어진 영영풀이에 해당하는 것은?",
    "match_en_r": "다음 글의 내용과 일치하는 것은?",
    "match_ko_r": "다음 글의 내용과 일치하는 것은?",
    "match_en_w": "다음 글의 내용과 일치하지 않는 것은?",
    "match_ko_w": "다음 글의 내용과 일치하지 않는 것은?",
    "purpose": "다음 글의 목적으로 가장 적절한 것은?",
    "mood": "다음 글에 드러난 등장인물(글쓴이)의 심경으로 가장 적절한 것은?",
    "refer": "밑줄 친 ①~⑤ 중에서 가리키는 대상이 나머지 넷과 다른 것은?",
    "gram_explain": "다음 글의 밑줄 친 ⓐ~ⓔ에 대한 어법 설명으로 옳지 않은 것은?",
    "right_sent": "다음 중 어법상 옳은 문장은?",
    "link2": "다음 글의 빈칸 (A), (B)에 들어갈 말로 가장 적절한 것은?",
    "sa_grammar": "[서술형] 다음 글의 밑줄 친 ⓐ~ⓔ 중 어법상 틀린 것 2개를 찾아 기호를 쓰고 바르게 고쳐 쓰시오.",
    "sa_arrange": "[서술형] 윗글의 밑줄 친 우리말과 같은 뜻이 되도록 <보기>의 단어를 모두 한 번씩 써서 (A)에 들어갈 문장을 완성하시오.",
    "sa_summary": "[서술형] 다음 글의 내용을 한 문장으로 요약할 때, 빈칸 (A), (B)에 들어갈 말을 본문에서 찾아 그대로 쓰시오.",
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


# ---------------------------------------------------------------- 섞어서 만들기 (랜덤)

MAX_MIX = 500


def feasible(text: str, t: str) -> bool:
    return len(split_sentences(text)) >= MIN_SENTENCES.get(t, 1)


def plan_mix(passages: list[dict], types: list[str], n: int, seed: int) -> list[dict]:
    """고른 유형으로 n문항을 지문들에 고르게 나눠 배정하고 순서를 섞음. [{"p": 지문 번호, "type": 유형, "k": 몇 번째}, …]
    - 유형은 고르게 (n을 유형 수로 나눠 돌아가며), 지문도 고르게 (가장 적게 쓴 지문부터)
    - 같은 지문·같은 유형은 다른 조합을 다 쓴 뒤에야 두 번째(k=1)로 (그때는 AI가 다른 문제로 다시 만듦)
    - 문장 수가 모자라는 지문에는 그 유형을 안 줌"""
    rng = random.Random(seed)
    want = [types[i % len(types)] for i in range(n)]
    rng.shuffle(want)
    used = {i: 0 for i in range(len(passages))}
    cnt: dict[tuple[int, str], int] = {}
    ok = {(i, t) for i in used for t in types if feasible(passages[i]["text"], t)}
    if not ok:
        return []
    plan = []
    for t in want:
        cands = [(i, t) for i in used if (i, t) in ok]
        if not cands or min(cnt.get(c, 0) for c in cands) > min(cnt.get(c, 0) for c in ok):
            cands = list(ok)  # 이 유형은 다 썼으면 덜 쓴 다른 조합으로
        low = min((cnt.get(c, 0), used[c[0]]) for c in cands)
        i, t2 = rng.choice([c for c in cands if (cnt.get(c, 0), used[c[0]]) == low])
        plan.append({"p": i, "type": t2, "k": cnt.get((i, t2), 0)})
        cnt[(i, t2)] = cnt.get((i, t2), 0) + 1
        used[i] += 1
    rng.shuffle(plan)
    return plan


def plan_counts(passages: list[dict], counts: dict[str, int], seed: int, shuffle: bool) -> list[dict]:
    """유형별 문항 수(counts)만큼 지문에 고르게 나눠 배정. shuffle=False면 지문 순서 → 유형 순서대로."""
    rng = random.Random(seed)
    used = {i: 0 for i in range(len(passages))}
    cnt: dict[tuple[int, str], int] = {}
    plan = []
    for t, n in counts.items():
        cands = [i for i in used if feasible(passages[i]["text"], t)]
        for _ in range(max(0, int(n)) if cands else 0):
            low = min((cnt.get((i, t), 0), used[i]) for i in cands)
            i = rng.choice([i for i in cands if (cnt.get((i, t), 0), used[i]) == low])
            plan.append({"p": i, "type": t, "k": cnt.get((i, t), 0)})
            cnt[(i, t)] = cnt.get((i, t), 0) + 1
            used[i] += 1
    order = list(counts)
    if shuffle:
        rng.shuffle(plan)
    else:
        plan.sort(key=lambda a: (a["p"], a["k"], order.index(a["type"])))
    return plan


def plan_total(passages: list[dict], types: list[str], n: int, seed: int, shuffle: bool) -> list[dict]:
    """전체 n문항을 고른 유형으로 고르게(랜덤) 나눠 배정. shuffle=False면 지문 순서대로."""
    plan = plan_mix(passages, types, n, seed)
    if not shuffle:
        plan.sort(key=lambda a: (a["p"], a["k"], types.index(a["type"]) if a["type"] in types else 99))
    return plan


def mix_capacity(passages: list[dict], types: list[str]) -> int:
    return sum(1 for p in passages for t in types if feasible(p["text"], t))


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
    choices = [_s(c) for c in (raw.get("choices") or [])][:5] if isinstance(raw.get("choices"), list) else []
    if t in ("topic", "title", "gist", "purpose", "mood", "right_sent", "def_pick") or t in MATCH4:
        q = {"type": t, "choices": choices, "answer": _idx(raw.get("answer")), "explain": explain}
        if t in MATCH4:
            q["ask"] = MATCH4[t]
        if t == "def_pick":
            q["definition"] = _s(raw.get("definition"))
            q["found"] = bool(choices) and all(re.search(r"(?<![A-Za-z])" + re.escape(c) + r"(?![A-Za-z])", passage, re.I) for c in choices)
        return q
    if t in ("blank_word", "blank_sent", "implied"):
        phrase = _s(raw.get({"blank_word": "word", "blank_sent": "sentence", "implied": "phrase"}[t]))
        m = re.search(r"(?<![A-Za-z0-9'])" + re.escape(phrase) + r"(?![A-Za-z0-9'])", passage) if phrase else None
        return {"type": t, "phrase": phrase, "before": passage[: m.start()] if m else "", "after": passage[m.end():] if m else "",
                "found": bool(m), "choices": choices, "answer": _idx(raw.get("answer")), "explain": explain}
    if t in ("refer", "gram_explain"):
        marks = [{"text": str(m.get("text", "")).strip(), "wrong": bool(m.get("wrong")), "note": _s(m.get("note") or m.get("target")),
                  "fix": _s(m.get("fix")), "why": ""} for m in (raw.get("marks") or []) if isinstance(m, dict)]
        return {"type": t, "passage": passage, "marks": marks, "explain": explain}
    if t == "link2":
        pairs = []
        for c in (raw.get("choices") or [])[:5]:
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                pairs.append([_s(c[0]), _s(c[1])])
            elif isinstance(c, dict):
                pairs.append([_s(c.get("A")), _s(c.get("B"))])
        return {"type": t, "passage": (raw.get("passage") or "").strip(), "choices": pairs, "answer": _idx(raw.get("answer")), "explain": explain}
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
    if t in ("vocab", "grammar", "sa_grammar"):
        marks = [{"text": str(m.get("text", "")).strip(), "wrong": bool(m.get("wrong")), "fix": _s(m.get("fix")), "why": _s(m.get("why"))}
                 for m in (raw.get("marks") or []) if isinstance(m, dict)]
        return {"type": t, "passage": (raw.get("passage") or "").strip(), "marks": marks, "explain": explain}
    if t == "definition":
        marks = [{"text": str(m.get("text", "")).strip(), "definition": _s(m.get("definition")), "wrong": bool(m.get("wrong")),
                  "fix": _s(m.get("fix")), "why": ""} for m in (raw.get("marks") or []) if isinstance(m, dict)]
        return {"type": t, "passage": passage, "marks": marks, "explain": explain}
    if t == "sa_arrange":
        sent = _s(raw.get("sentence"))
        m = re.search(re.escape(sent), passage) if sent else None
        words = arrange_words(sent, seed) if m else []
        return {"type": t, "sentence": sent, "korean": _s(raw.get("korean")), "before": passage[: m.start()] if m else "",
                "after": passage[m.end():] if m else "", "found": bool(m), "words": words, "point": _s(raw.get("point")), "explain": explain}
    if t == "sa_summary":
        answers = [_s(x) for x in (raw.get("answers") or [])][:2]
        found = len(answers) == 2 and all(re.search(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", passage, re.I) for a in answers if a)
        return {"type": t, "summary": _s(raw.get("summary")), "answers": answers, "found": bool(found), "explain": explain}
    return None


def arrange_words(sentence: str, seed: int) -> list[str]:
    """문장 → 섞은 단어 (첫 단어는 소문자로, 끝 문장부호는 뺌)."""
    ws = sentence.rstrip(".!?").split()
    if ws and not (ws[0] == "I" or ws[0][1:2].isupper()):
        ws[0] = ws[0][:1].lower() + ws[0][1:]
    ws = [w.strip(",;:\"“”") for w in ws if w.strip(",;:\"“”")]
    rnd = random.Random(seed)
    for _ in range(5):
        rnd.shuffle(ws)
        if len(ws) < 3 or ws != sentence.split():
            break
    return ws


def validate(q: dict | None) -> list[str]:
    if not q:
        return ["형식이 맞지 않습니다."]
    t, name = q["type"], TYPES.get(q["type"], q["type"])
    errs = []
    if t in CHOICE_TYPES or t in ("summary", "link2"):
        if len(q.get("choices") or []) != 5 or any(not c or (isinstance(c, list) and not all(c)) for c in q["choices"]):
            errs.append(f"{name}: 보기가 5개여야 합니다.")
        if not q.get("answer"):
            errs.append(f"{name}: 정답 번호(1~5)가 없습니다.")
    if t == "blank" and not q.get("found"):
        errs.append("빈칸 추론: 빈칸으로 만들 구절(phrase)을 지문에서 글자 그대로 찾지 못했습니다.")
    if t in ("blank_word", "blank_sent", "implied") and not q.get("found"):
        errs.append(f"{name}: 지문에서 그 부분(글자 그대로)을 찾지 못했습니다.")
    if t == "blank_word" and len((q.get("phrase") or "").split()) != 1:
        errs.append("빈칸(단어): 빈칸은 한 단어여야 합니다.")
    if t == "blank_sent" and len((q.get("phrase") or "").split()) < 6:
        errs.append("빈칸(문장): 빈칸은 문장(6단어 이상)이어야 합니다.")
    if t == "def_pick":
        if not q.get("definition"):
            errs.append("영영풀이: 풀이가 없습니다.")
        if not q.get("found"):
            errs.append("영영풀이: 보기 낱말이 모두 지문에 있어야 합니다.")
    if t == "link2" and not ("(A)" in q.get("passage", "") and "(B)" in q.get("passage", "") and q["passage"].index("(A)") < q["passage"].index("(B)")):
        errs.append("연결어 2빈칸: 지문에 (A), (B) 자리가 차례로 있어야 합니다.")
    if t in ("refer", "gram_explain"):
        marks = q.get("marks") or []
        if len(marks) != 5:
            errs.append(f"{name}: 밑줄이 5개여야 하는데 {len(marks)}개입니다.")
        elif place_marks(q.get("passage", ""), marks) is None:
            errs.append(f"{name}: 밑줄 부분을 지문에서 순서대로 찾지 못했습니다.")
        if sum(m["wrong"] for m in marks) != 1:
            errs.append(f"{name}: 답(다른 것·틀린 설명)은 정확히 1개여야 합니다.")
        if t == "gram_explain" and any(not m.get("note") for m in marks):
            errs.append("어법 분석: 비어 있는 설명이 있습니다.")
    if t == "summary" and ("(A)" not in q.get("summary", "") or "(B)" not in q.get("summary", "")):
        errs.append("요약문 완성: 요약문에 (A)와 (B) 빈칸이 모두 있어야 합니다.")
    if t == "irrelevant" and len(q.get("numbered") or []) != 5:
        errs.append("무관한 문장: 지문이 짧거나 끼워 넣을 문장(sentence)이 없습니다.")
    if t == "order" and set(q.get("parts", {})) != set("ABC"):
        errs.append("순서 배열: 덩어리가 3개가 아닙니다.")
    if t == "insert" and len(q.get("gaps") or []) != 5:
        errs.append("문장 삽입: 빈자리 후보가 5개가 아닙니다.")
    if t == "definition":
        marks = q.get("marks") or []
        if len(marks) != 5:
            errs.append(f"영영풀이: 밑줄이 5개여야 하는데 {len(marks)}개입니다.")
        elif place_marks(q.get("passage", ""), marks) is None:
            errs.append("영영풀이: 밑줄 단어를 지문에서 순서대로(글자 그대로) 찾지 못했습니다.")
        if sum(m["wrong"] for m in marks) != 1:
            errs.append("영영풀이: 적절하지 않은 풀이는 정확히 1개여야 합니다.")
        if any(not m.get("definition") for m in marks):
            errs.append("영영풀이: 비어 있는 풀이가 있습니다.")
    if t == "sa_arrange":
        if not q.get("found"):
            errs.append("배열 영작: 문장(sentence)을 지문에서 글자 그대로 찾지 못했습니다.")
        elif not 6 <= len(q.get("words") or []) <= 22:
            errs.append("배열 영작: 문장은 6~22단어여야 합니다.")
        if not q.get("korean"):
            errs.append("배열 영작: 우리말 뜻(korean)이 없습니다.")
    if t == "sa_summary":
        if "(A)" not in q.get("summary", "") or "(B)" not in q.get("summary", ""):
            errs.append("서술형 요약: 요약문에 (A)와 (B)가 있어야 합니다.")
        if not q.get("found"):
            errs.append("서술형 요약: 답(answers) 2개를 지문에서 글자 그대로 찾지 못했습니다.")
    if t in ("grammar", "sa_grammar"):
        marks = q.get("marks") or []
        need = 2 if t == "sa_grammar" else 1
        if len(marks) != 5:
            errs.append(f"{name}: 밑줄이 5개여야 하는데 {len(marks)}개입니다.")
        elif place_marks(q.get("passage", ""), marks) is None:
            errs.append(f"{name}: 밑줄 부분을 지문에서 순서대로 찾지 못했습니다.")
        if sum(m["wrong"] for m in marks) != need:
            errs.append(f"{name}: 틀린 밑줄은 정확히 {need}개여야 합니다.")
        if any(m["wrong"] and not m.get("fix") for m in marks):
            errs.append(f"{name}: 틀린 밑줄의 고친 형태(fix)가 없습니다.")
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
    t = q["type"]
    if t == "sa_grammar":
        return ", ".join(f"{LETTERS[i]} {m['text']} → {m['fix']}" for i, m in enumerate(q.get("marks") or []) if m["wrong"])
    if t == "sa_arrange":
        return q.get("sentence", "")
    if t == "sa_summary":
        ans = q.get("answers") or ["", ""]
        return f"(A) {ans[0]}  (B) {ans[1] if len(ans) > 1 else ''}"
    if t in ("grammar", "definition", "refer", "gram_explain"):
        wrong = [i for i, m in enumerate(q.get("marks") or []) if m["wrong"]]
        return CIRCLED[wrong[0]] if wrong else "?"
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
    "blank": '"blank": {"phrase": "지문 속 핵심 구(2~6단어, 글자 그대로)", "choices": ["영어 보기 5개"], "answer": 1~5, "explain": "..."}  // 정답 보기는 phrase와 같거나 같은 뜻',
    "irrelevant": '"irrelevant": {"sentence": "지문 소재와 관련 있어 보이지만 글의 흐름과 무관한 영어 문장 하나", "explain": "..."}',
    "summary": '"summary": {"summary": "글 전체를 요약한 영어 한 문장, 빈칸 두 곳을 (A), (B)로", "choices": [["A보기","B보기"] 5쌍], "answer": 1~5, "explain": "..."}',
    "match": '"match": {"ask": "wrong" 또는 "right", "choices": ["한국어 진술 5개, 지문 순서대로"], "answer": 1~5, "explain": "..."}  // wrong이면 불일치가 정확히 1개',
    "grammar": '"grammar": {"passage": "어법상 틀린 곳 하나를 끼워 넣은 지문 전체 (나머지는 원문 그대로)", "marks": [{"text": "밑줄 부분(지문 글자 그대로, 1~3단어)", "wrong": false, "fix": "", "why": "어법 포인트"}, ... 정확히 5개, 앞에서부터 순서대로, 틀린 것 정확히 1개(wrong: true, fix: 올바른 형태)], "explain": "..."}  // 수일치·관계사·분사·to부정사/동명사·대명사·병렬·시제·태 같은 내신 단골 어법',
    "definition": '"definition": {"marks": [{"text": "지문 속 내용어(글자 그대로, 1단어)", "definition": "영어 풀이 (영영사전식, 10단어 안팎)", "wrong": false, "fix": ""}, ... 정확히 5개, 지문에 나오는 순서대로, 적절하지 않은 풀이 정확히 1개(wrong: true, 그럴듯하지만 이 문맥의 뜻이 아닌 풀이, fix: 맞는 풀이)], "explain": "..."}',
    "sa_grammar": '"sa_grammar": {"passage": "어법상 틀린 곳 두 개를 끼워 넣은 지문 전체 (나머지는 원문 그대로)", "marks": [{"text": "밑줄 부분(지문 글자 그대로, 1~3단어)", "wrong": false, "fix": "", "why": "어법 포인트"}, ... 정확히 5개, 순서대로, 틀린 것 정확히 2개(wrong: true, fix: 올바른 형태)], "explain": "..."}',
    "sa_arrange": '"sa_arrange": {"sentence": "지문 속 구문이 중요한 문장 하나 (글자 그대로, 8~18단어)", "korean": "그 문장의 자연스러운 우리말 뜻", "point": "출제 구문 (예: 관계대명사 what, 가주어-진주어)", "explain": "..."}',
    "sa_summary": '"sa_summary": {"summary": "글 전체를 요약한 영어 한 문장, 빈칸 두 곳을 (A), (B)로", "answers": ["(A) 답: 지문에 나온 말 글자 그대로 1~2단어", "(B) 답"], "explain": "..."}',
    "blank_word": '"blank_word": {"word": "글의 핵심을 담은 지문 속 낱말 하나(글자 그대로, 한 단어)", "choices": ["영어 낱말 보기 5개"], "answer": 1~5, "explain": "..."}  // 정답 보기는 word 와 같거나 같은 뜻의 한 단어',
    "blank_sent": '"blank_sent": {"sentence": "글의 핵심을 담은 지문 속 문장 하나(첫 문장 말고, 글자 그대로, 마침표 빼고)", "choices": ["영어 문장(절) 보기 5개"], "answer": 1~5, "explain": "..."}  // 정답 보기는 그 문장을 다른 말로 바꿔 쓴 것',
    "implied": '"implied": {"phrase": "지문 속 비유적·함축적 표현(2~8단어, 글자 그대로)", "choices": ["그 표현이 이 글에서 뜻하는 바를 풀어 쓴 영어 보기 5개"], "answer": 1~5, "explain": "..."}',
    "def_pick": '"def_pick": {"definition": "지문 속 낱말 하나의 영어 풀이(영영사전식, 8~15단어)", "choices": ["지문에 나온 낱말 5개(글자 그대로, 한 단어씩, 지문 순서대로)"], "answer": 풀이에 맞는 낱말 번호 1~5, "explain": "..."}',
    "match_en_r": '"match_en_r": {"choices": ["지문 내용에 대한 영어 진술 5개, 지문 순서대로"], "answer": 1~5, "explain": "..."}  // 내용과 일치하는 것 정확히 1개',
    "match_ko_r": '"match_ko_r": {"choices": ["지문 내용에 대한 한국어 진술 5개, 지문 순서대로"], "answer": 1~5, "explain": "..."}  // 내용과 일치하는 것 정확히 1개',
    "match_en_w": '"match_en_w": {"choices": ["지문 내용에 대한 영어 진술 5개, 지문 순서대로"], "answer": 1~5, "explain": "..."}  // 내용과 일치하지 않는 것 정확히 1개',
    "match_ko_w": '"match_ko_w": {"choices": ["지문 내용에 대한 한국어 진술 5개, 지문 순서대로"], "answer": 1~5, "explain": "..."}  // 내용과 일치하지 않는 것 정확히 1개',
    "purpose": '"purpose": {"choices": ["글을 쓴 목적 한국어 보기 5개 (예: ~하려고)"], "answer": 1~5, "explain": "..."}',
    "mood": '"mood": {"choices": ["등장인물·글쓴이의 심경(변화) 영어 보기 5개 (예: nervous → relieved, 또는 grateful)"], "answer": 1~5, "explain": "..."}',
    "refer": '"refer": {"marks": [{"text": "지칭어(대명사나 명사구, 지문 글자 그대로, 1~3단어)", "target": "가리키는 대상(한국어)", "wrong": false}, ... 정확히 5개, 지문 순서대로, 가리키는 대상이 나머지 넷과 다른 것 정확히 1개(wrong: true)], "explain": "..."}',
    "gram_explain": '"gram_explain": {"marks": [{"text": "어법 포인트가 있는 부분(지문 글자 그대로, 1~4단어)", "note": "그 부분에 대한 어법 설명 한국어 한 줄", "wrong": false, "fix": ""}, ... 정확히 5개, 지문 순서대로, 설명이 틀린 것 정확히 1개(wrong: true, fix: 맞는 설명)], "explain": "..."}',
    "right_sent": '"right_sent": {"choices": ["지문 문장을 바탕으로 한 영어 문장 5개 — 어법상 옳은 것 정확히 1개, 나머지 4개는 내신 단골 어법 오류가 하나씩"], "answer": 1~5, "explain": "..."}',
    "link2": '"link2": {"passage": "지문 전체를 원문 그대로 쓰되 연결어 두 자리를 (A), (B)로 바꾼 것 (연결어가 하나뿐이거나 없으면 알맞은 문장 앞에 자리를 만듦)", "choices": [["(A) 연결어", "(B) 연결어"] 5쌍], "answer": 1~5, "explain": "..."}',
    "vocab": '"vocab": {"passage": "낱말 하나를 문맥과 반대·부적절한 낱말로 바꾼 지문 전체", "marks": [{"text": "밑줄 낱말(지문 글자 그대로, 1~2단어)", "wrong": false, "fix": "", "why": "..."}, ... 정확히 5개, 앞에서부터 순서대로, 틀린 것 정확히 1개(wrong: true, fix: 원래 알맞은 낱말)], "explain": "..."}',
}


GRAMMAR_TYPES = ("grammar", "sa_grammar", "sa_arrange")


def grammar_note(target: str, types: list[str]) -> str:
    """목표어법이 있을 때 어법·서술형 유형에 붙이는 지시."""
    target = (target or "").strip()
    if not target or not any(t in GRAMMAR_TYPES for t in types):
        return ""
    out = [f"\n목표어법: {target}  (이번 시험 범위의 핵심 문법)",
           "- 지문에서 목표어법이 실제로 쓰인 곳을 먼저 찾으세요. 지문에 없는 문법을 억지로 만들어 넣지 마세요."]
    if "grammar" in types:
        out.append("- grammar: 틀린 곳(wrong: true) 1개는 목표어법이 쓰인 부분에 만드세요. 나머지 밑줄도 가능하면 목표어법 관련 부분을 포함하세요.")
    if "sa_grammar" in types:
        out.append("- sa_grammar: 틀린 곳 2개는 목표어법이 쓰인 부분에 만드세요 (지문에 그런 곳이 하나뿐이면 1개만 목표어법, 나머지는 다른 내신 단골 어법).")
    if "sa_arrange" in types:
        out.append("- sa_arrange: sentence는 목표어법이 쓰인 지문 문장으로 고르고, point에 그 목표어법 이름을 쓰세요.")
    out.append("- 지문에 목표어법이 전혀 없으면 위 지시는 무시하고 평소처럼 내신 단골 어법으로 내되, why/point에 실제 출제한 어법을 정확히 쓰세요.")
    return "\n".join(out)


def ai_prompt(passage: str, types: list[str], level: str, fix_notes: list[str] | None = None, target: str = "") -> str:
    lines = [f"대상: {level or '고등학생'}", "아래 지문으로 다음 유형의 문제를 하나씩 만드세요. 출력은 유형 이름을 키로 하는 JSON 객체입니다.", "{"]
    lines += ["  " + FORMATS[t] + "," for t in types]
    lines.append("}")
    note = grammar_note(target, types)
    if note:
        lines.append(note)
    if fix_notes:
        lines.append("\n직전 답의 문제점 (이 유형들은 규칙에 맞게 다시):\n- " + "\n- ".join(fix_notes))
    lines.append("\n지문:\n" + passage.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------- 새 유형 화면용 (PDF·워드·온라인 시험 공통)

def view(q: dict, text: str) -> dict | None:
    """어법·영영풀이·서술형 → {kind, stem, passage([u]밑줄), box, box_title, choices, lines}. 예전 유형은 None."""
    t = q["type"]
    if t in NEW_VIEW_TYPES:
        return _view_new(q, text)
    if t not in ("grammar", "definition") + SA_TYPES:
        return None
    out = {"kind": "sa" if t in SA_TYPES else "mc", "stem": STEMS[t], "passage": "", "box": "", "box_title": "", "choices": [], "lines": 0}
    if t in ("grammar", "sa_grammar", "definition"):
        sym = CIRCLED if t == "grammar" else LETTERS
        pieces = place_marks(q.get("passage", ""), q.get("marks") or []) or [{"text": q.get("passage", ""), "mark": None}]
        out["passage"] = "".join(pc["text"] if pc["mark"] is None else f"{sym[pc['mark']]}[u]{pc['text']}[/u]" for pc in pieces)
    if t == "definition":
        out["choices"] = [f"{LETTERS[i]} {m['text']}: {m.get('definition', '')}" for i, m in enumerate(q.get("marks") or [])]
    elif t == "sa_grammar":
        out["lines"] = 2
    elif t == "sa_arrange":
        out["passage"] = f"{q.get('before', '')}(A) [u]{q.get('korean', '')}[/u]{q.get('after', '')}"
        out["box"], out["box_title"] = " / ".join(q.get("words") or []), "<보기>"
        out["lines"] = 2
    elif t == "sa_summary":
        out["passage"] = text
        out["box"], out["box_title"] = q.get("summary", "").replace("(A)", "(A) ________").replace("(B)", "(B) ________"), "요약문"
        out["lines"] = 1
    return out


def _view_new(q: dict, text: str) -> dict:
    t = q["type"]
    out = {"kind": "mc", "stem": STEMS[t], "passage": text, "box": "", "box_title": "", "choices": list(q.get("choices") or []), "lines": 0}
    if t in ("blank_word", "blank_sent"):
        out["passage"] = f"{q.get('before', '')}{'_' * (8 if t == 'blank_word' else 30)}{q.get('after', '')}"
    elif t == "implied":
        out["passage"] = f"{q.get('before', '')}[u]{q.get('phrase', '')}[/u]{q.get('after', '')}"
    elif t == "def_pick":
        out["box"], out["box_title"] = q.get("definition", ""), "영영풀이"
    elif t in ("refer", "gram_explain"):
        sym = CIRCLED if t == "refer" else LETTERS
        pieces = place_marks(q.get("passage", ""), q.get("marks") or []) or [{"text": q.get("passage", ""), "mark": None}]
        out["passage"] = "".join(pc["text"] if pc["mark"] is None else f"{sym[pc['mark']]}[u]{pc['text']}[/u]" for pc in pieces)
        out["choices"] = [] if t == "refer" else [f"{LETTERS[i]} {m['text']}: {m.get('note', '')}" for i, m in enumerate(q.get("marks") or [])]
    elif t == "right_sent":
        out["passage"] = ""
    elif t == "link2":
        out["passage"] = (q.get("passage") or "").replace("(A)", "(A) ________").replace("(B)", "(B) ________")
        out["choices"] = [f"(A) {a} ······ (B) {b}" for a, b in (q.get("choices") or [])]
    return out


def rich(text: str):
    import html as _h

    from markupsafe import Markup

    t = _h.escape(text or "").replace("[u]", "<u>").replace("[/u]", "</u>")
    return Markup(t.replace("\n", "<br>"))