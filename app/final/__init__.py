"""직전대비 — 본문 숙지 확인 테스트.

지문마다 5단계(단어 → 내용 → 핵심 표현 → 문장 구조 → 글 전체)로 본문을 정말 외웠는지 확인하는 시험지.
AI(Gemini)는 단어·OX·주제·빈칸·어법·배열·영작·해석을 만들고, 순서 배열·문장 넣기는 본문 문장으로 사이트가 직접 만든다.
AI 답은 본문과 맞는지 검사해서(단어가 본문에 있나, 빈칸 답이 그 문장에 있나 …) 안 맞는 건 버린다.
"""
import random
import re

MAX_PASSAGES = 10
LEVELS = ["", "중3", "고1", "고2", "고3"]
CIRCLED = "①②③④⑤"

# 단계: 이름, 설명, 문항 수, 문항당 배점(4단계는 문항별)
STAGES = {
    "vocab": {"no": 1, "name": "단어", "sub": "뜻과 철자", "n": 10, "pt": 2},
    "content": {"no": 2, "name": "내용", "sub": "흐름과 세부 내용", "n": 6, "pt": 3},
    "phrase": {"no": 3, "name": "핵심 표현", "sub": "본문 그대로 떠올리기", "n": 7, "pt": 3},
    "struct": {"no": 4, "name": "문장 구조", "sub": "어법·배열·영작", "n": 6, "pt": None},
    "whole": {"no": 5, "name": "글 전체", "sub": "순서와 문장 넣기 (실전)", "n": 2, "pt": 8},
}
ORDER_CHOICES = ["(A)-(C)-(B)", "(B)-(A)-(C)", "(B)-(C)-(A)", "(C)-(A)-(B)", "(C)-(B)-(A)"]

SYSTEM = """당신은 한국 고등학교 영어 내신 직전대비 문제를 만드는 선생님입니다. 학생이 본문을 정말 외웠는지 확인하는 '본문 숙지 확인 테스트' 재료를 만듭니다.
- 모든 문항은 주어진 본문에서만 나옵니다. 본문에 없는 단어·문장·사실을 지어내지 않습니다.
- 내신에 실제로 잘 나오는 것을 고릅니다: 핵심 어휘, 구동사·연어(end up, choose A over B), 관계사·수 일치·분사·to부정사 같은 어법 포인트, 연결어.
- 한국어는 자연스럽고 짧게 씁니다. JSON 객체 하나로만 답합니다."""


def split_sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    parts = re.split(r'(?<=[.!?])["”’)]?\s+(?=["“(]?[A-Z0-9])', text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and len(p.split()) < 3:  # 너무 짧은 조각은 앞 문장에 붙임
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def prompt(text: str, level: str = "") -> str:
    ss = split_sentences(text)
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(ss, 1))
    return f"""[본문] (문장 번호 붙임){f'  · 대상: {level}' if level else ''}
{numbered}

아래 JSON 을 채우세요. 문장 번호 n 은 위 [번호] 입니다.
{{
 "topic_ko": "글의 주제 한국어 한 줄",
 "translations": ["[1] 문장의 자연스러운 해석", "... 문장 수({len(ss)}개)만큼 순서대로"],
 "vocab": [{{"word": "본문에 나온 형태 그대로의 핵심 어휘", "meaning": "본문 뜻(짧게)"}}],   // 12개. 쉬운 단어 말고 시험에 나올 단어
 "ox": [{{"ko": "본문 내용에 대한 한국어 진술", "answer": "O 또는 X", "why": "X면 맞는 내용 한 줄"}}],   // 5개, O와 X 섞어서
 "topic": {{"choices": ["영어 선택지 5개"], "answer": 1}},   // 정답 하나, 나머지는 그럴듯한 오답
 "blanks": [{{"n": 1, "answer": "그 문장에 실제로 있는 한 단어"}}],   // 9개, 서로 다른 문장. 구동사 부사·연어·핵심 동사 위주
 "fix": [{{"n": 2, "wrong": "그 문장에서 어법 한 곳만 틀리게 바꾼 문장(전체)", "error": "틀린 부분", "fix": "바른 형태", "why": "이유 한 줄"}}],   // 3개, 내신 단골 어법
 "arrange": [{{"n": 4, "ko": "그 문장(또는 그 일부 절)의 해석", "chunks": ["바른 순서의 덩어리 4~5개 — 이어 붙이면 영어 정답 문장"]}}],   // 2개
 "compose": {{"n": 9, "ko": "영작할 우리말", "conditions": ["조건 3개 (사용할 구문, 단어 수 등)"], "answer": "모범 답", "grading": "부분 점수 채점 기준 한 줄"}},
 "insert_n": 6   // 문장 넣기 문제로 쓰기 좋은 문장 번호 (연결어·지시어가 있어 자리가 하나로 정해지는 문장, 첫 문장 말고)
}}"""


def _has_word(sentence: str, word: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", sentence, re.I))


def _blank(sentence: str, word: str) -> str:
    """첫 번째 그 단어를 빈칸(첫 글자 힌트)으로."""
    return re.sub(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", "\x00", sentence, count=1, flags=re.I)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join(str(t).lower().split()))


def clean(raw, text: str, seed: int = 0) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    ss = split_sentences(text)
    n = len(ss)
    rnd = random.Random(seed or len(text))
    lower = text.lower()
    out: dict = {"sentences": ss, "topic_ko": str(raw.get("topic_ko") or "")[:120]}
    tr = [str(x) for x in (raw.get("translations") or [])]
    out["translations"] = (tr + [""] * n)[:n]

    # 1 단어: 본문에 있는 것만, 앞 6개 영→한, 다음 4개 한→영
    seen, vocab = set(), []
    for v in raw.get("vocab") or []:
        if not isinstance(v, dict):
            continue
        w, m = str(v.get("word") or "").strip(), str(v.get("meaning") or "").strip()
        if w and m and w.lower() not in seen and w.lower() in lower and len(w) > 2:
            seen.add(w.lower())
            vocab.append({"word": w, "meaning": m[:30]})
    out["vocab"] = vocab[:10]

    # 2 내용
    ox = []
    for x in raw.get("ox") or []:
        if isinstance(x, dict) and str(x.get("answer", "")).strip().upper() in ("O", "X") and x.get("ko"):
            ox.append({"ko": str(x["ko"])[:120], "answer": str(x["answer"]).strip().upper(), "why": str(x.get("why") or "")[:100]})
    out["ox"] = ox[:5]
    tp = raw.get("topic") or {}
    ch = [str(c) for c in (tp.get("choices") or [])][:5] if isinstance(tp, dict) else []
    try:
        ans = int(tp.get("answer"))
    except (TypeError, ValueError, AttributeError):
        ans = 0
    out["topic"] = {"choices": ch, "answer": ans} if len(ch) == 5 and 1 <= ans <= 5 else None

    # 3 핵심 표현: 답이 그 문장에 실제로 있어야
    blanks, used = [], set()
    for b in raw.get("blanks") or []:
        try:
            k = int(b.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        w = str(b.get("answer") or "").strip()
        if not (1 <= k <= n) or k in used or not re.fullmatch(r"[A-Za-z'-]+", w) or not _has_word(ss[k - 1], w):
            continue
        used.add(k)
        before, _, after = _blank(ss[k - 1], w).partition("\x00")
        blanks.append({"n": k, "answer": w, "before": before, "after": after})
    out["blanks"] = sorted(blanks[:7], key=lambda x: x["n"])

    # 4 문장 구조
    fixes = []
    for f in raw.get("fix") or []:
        try:
            k = int(f.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        wrong, err, fx = str(f.get("wrong") or "").strip(), str(f.get("error") or "").strip(), str(f.get("fix") or "").strip()
        if 1 <= k <= n and wrong and err and fx and err.lower() != fx.lower() and _norm(wrong) != _norm(ss[k - 1]) and err.lower() in wrong.lower():
            fixes.append({"n": k, "wrong": wrong, "error": err, "fix": fx, "why": str(f.get("why") or "")[:100]})
    out["fix"] = fixes[:3]
    arr = []
    for a in raw.get("arrange") or []:
        chunks = [str(c).strip() for c in (a.get("chunks") or []) if str(c).strip()] if isinstance(a, dict) else []
        if 3 <= len(chunks) <= 6 and a.get("ko") and all(_norm(c) in _norm(text) or len(c.split()) <= 2 for c in chunks):
            shown = chunks[:]
            for _ in range(10):
                rnd.shuffle(shown)
                if shown != chunks:
                    break
            arr.append({"ko": str(a["ko"])[:120], "chunks": shown, "answer": " ".join(chunks)})
    out["arrange"] = arr[:2]
    c = raw.get("compose") or {}
    out["compose"] = ({"ko": str(c["ko"])[:150], "conditions": [str(x)[:60] for x in (c.get("conditions") or [])][:3],
                       "answer": str(c.get("answer") or "")[:300], "grading": str(c.get("grading") or "")[:150]}
                      if isinstance(c, dict) and c.get("ko") and c.get("answer") else None)

    # 5 글 전체: 사이트가 직접
    out["order"] = make_order(ss, rnd)
    try:
        ins = int(raw.get("insert_n") or 0)
    except (TypeError, ValueError):
        ins = 0
    out["insert"] = make_insert(ss, ins)
    return out


def make_order(ss: list[str], rnd: random.Random) -> dict | None:
    """주어진 글(앞 1~2문장) + (A)(B)(C) 세 덩어리."""
    if len(ss) < 5:
        return None
    g = 2 if len(ss) >= 8 else 1
    rest = ss[g:]
    k = len(rest)
    cut = [round(k / 3), round(2 * k / 3)]
    parts = [rest[:cut[0]], rest[cut[0]:cut[1]], rest[cut[1]:]]
    if any(not p for p in parts):
        return None
    perm = rnd.choice([p for p in ([1, 0, 2], [0, 2, 1], [2, 0, 1], [1, 2, 0], [2, 1, 0])])  # 정답이 (A)-(B)-(C)가 아니게
    # perm[i] = 원래 i번째 덩어리에 붙일 이름 번호
    labels = "ABC"
    shown = sorted(range(3), key=lambda i: perm[i])
    answer = "-".join(f"({labels[perm[i]]})" for i in range(3))
    return {"given": " ".join(ss[:g]), "parts": [{"label": labels[perm[i]], "text": " ".join(parts[i])} for i in shown],
            "answer": ORDER_CHOICES.index(answer) + 1}


def make_insert(ss: list[str], k: int) -> dict | None:
    """k번째 문장을 빼고, 자리 ①~⑤ 를 둔 글."""
    if len(ss) < 6:
        return None
    if not 2 <= k <= len(ss):
        k = len(ss) // 2 + 1
    target = ss[k - 1]
    rest = ss[:k - 1] + ss[k:]
    g = k - 1  # 빠진 자리: rest[g] 앞 (끝이면 g == len(rest))
    g0 = max(1, min(g - 2, len(rest) - 4))
    gaps = list(range(g0, g0 + 5))
    if g not in gaps:
        return None
    lead = " ".join(rest[max(0, g0 - 2):g0])
    body = []
    for j, gap in enumerate(gaps):
        body.append(f"( {CIRCLED[j]} )")
        if gap < len(rest):
            body.append(rest[gap])
    return {"sentence": target, "text": (lead + " " + " ".join(body)).strip(), "answer": gaps.index(g) + 1}


def points(st: dict, stages: list[str]) -> dict:
    """단계별 문항·배점. 4단계: 어법 4점, 배열 4점, 영작 5점."""
    p = {}
    if "vocab" in stages:
        p["vocab"] = 2 * len(st["vocab"])
    if "content" in stages:
        p["content"] = 3 * (len(st["ox"]) + (1 if st["topic"] else 0))
    if "phrase" in stages:
        p["phrase"] = 3 * len(st["blanks"])
    if "struct" in stages:
        p["struct"] = 4 * len(st["fix"]) + 4 * len(st["arrange"]) + (5 if st["compose"] else 0)
    if "whole" in stages:
        p["whole"] = 8 * ((1 if st["order"] else 0) + (1 if st["insert"] else 0))
    return p
