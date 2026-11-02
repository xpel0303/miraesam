"""본문 워크북 (10단계) 규칙 — 교과서·부교재 본문 한 과를 10가지 연습지로.

  1 본문 해석지      영문 | 해석 (핵심 어휘 강조)
  2 빈칸 연습(우리말) 영문 보고 해석의 빈칸 채우기
  3 빈칸 연습(영문)   해석 보고 영문의 빈칸 채우기
  4 해석 연습        영문만, 해석 쓰기
  5 동사형 연습      (동사원형) 알맞게 고쳐 쓰기
  6 어법 선택형      [맞는 것 / 틀린 것] 고르기
  7 어색한 곳 찾기    문단마다 문맥상 어색한 낱말 3개 찾아 고치기
  8 순서배열         의미 덩어리를 섞어 주고 배열
  9 영작 연습        주어진 단어를 순서대로 써서 영작
 10 Check           문단 전체에 어법·어휘 선택, 빈칸을 섞은 종합 점검
문장 나누기·덩어리 섞기·문항 번호는 코드가, 해석·고를 낱말은 AI가 (문장 번호마다) 줍니다.
AI가 준 표시 위치는 원문에서 글자 그대로 찾을 수 있을 때만 씁니다 (못 찾으면 그 표시만 빼고 경고).
"""
import random
import re

from ..analysis.spec import split_sentences

MAX_PASSAGES = 30
CHUNK = 12  # AI 한 번에 보낼 문장 수

SECTIONS = [
    ("s1", "본문 해석지", "영문과 해석을 읽으며 문장의 의미를 파악해 보세요."),
    ("s2", "빈칸 연습 (우리말)", "영문을 보고 우리말 해석을 완성하시오."),
    ("s3", "빈칸 연습 (영문)", "우리말 해석을 보고 영문을 완성하시오."),
    ("s4", "해석 연습", "영어 문장을 읽고 우리말 해석을 쓰시오."),
    ("s5", "동사형 연습", "괄호 안에 주어진 단어를 알맞게 고쳐 쓰세요."),
    ("s6", "어법 선택형 연습", "괄호 안에서 어법상 알맞은 것을 골라 보세요."),
    ("s7", "어색한 곳 찾기 연습", "다음 글에서 문맥상 어색한 낱말 세 개를 찾아 쓰고, 알맞은 표현으로 고쳐 쓰시오."),
    ("s8", "순서배열 연습", "다음 우리말과 같은 뜻이 되도록 주어진 단어 및 어구를 알맞게 배열해 보세요."),
    ("s9", "영작 연습", "다음 우리말과 같은 뜻이 되도록 주어진 단어를 순서대로 사용하여 영작해 보세요."),
    ("s10", "Check", "어법·어휘를 고르고 빈칸을 채우며 본문을 점검해 보세요."),
]
SECTION_KEYS = [k for k, _, _ in SECTIONS]

SYSTEM_PROMPT = """당신은 한국 고등학교 교과서 본문 워크북(본문 해석지·빈칸·어법·배열·영작 연습지)을 만드는 교재 저자입니다. JSON 객체 하나로만 답합니다.
- 영어 표시(blank, verbs.text, grammar.text, odd.text, chunks)는 문장에 있는 글자를 하나도 바꾸지 않고 그대로 옮깁니다.
- 해석은 자연스러운 한국어로, 문장마다 따로."""


def prompt(sentences: list[tuple[int, str]], with_odd: bool, level: str = "", fix: list[str] | None = None) -> str:
    lines = [f"대상: {level or '고등학생'}", "아래 본문 문장마다 워크북 재료를 만들어 주세요.", "",
             "출력 형식:",
             "{",
             '  "sentences": [{',
             '    "n": 문장 번호,',
             '    "ko": "이 문장의 자연스러운 우리말 해석",',
             '    "en_blanks": ["시험에 잘 나오는 핵심 어휘·어구 1~3개 (문장 글자 그대로, 1~3단어씩, 문장 순서대로)"],',
             '    "ko_blanks": ["위 해석(ko)에서 빈칸으로 만들 핵심 낱말 1~3개 (ko 글자 그대로, 조사는 빼고)"],',
             '    "verbs": [{"text": "문장 속 동사 부분 그대로 (예: is also being produced)", "base": "괄호에 줄 원형 (예: also, produce)"}],  // 문장의 동사·준동사 거의 전부',
             '    "grammar": [{"text": "문장 속 어법 포인트 부분 그대로 (1~3단어)", "wrong": "그럴듯한 틀린 형태"}],  // 1~3개: 수일치·시제·태·분사·to부정사/동명사·관계사·접속사·형용사/부사·대명사',
             '    "chunks": ["순서배열용 의미 덩어리 (1~4단어). 덩어리를 띄어쓰기로 이으면 문장과 글자가 똑같아야 함"],',
             '    "keywords": ["영작 연습 힌트: 문장의 내용어를 원형으로, 문장에 나오는 순서대로 (예: planet, become, warmer)"]',
             "  }, ...]" + ("," if with_odd else ""),
             ]
    if with_odd:
        lines += ['  "odd": [{"text": "문단 속 내용어 하나 그대로 (문장 번호 n 의 문장에 있는 것)", "n": 문장 번호, "wrong": "문맥상 어색해지는 말 (주로 반의어, 같은 품사·형태)"}]  // 정확히 3개, 서로 다른 문장에서']
    lines += ["}", "", "본문 (문장 번호):"]
    lines += [f"[{n}] {s}" for n, s in sentences]
    if fix:
        lines += ["", "직전 답의 문제점 (고쳐서 다시):", *[f"- {x}" for x in fix[:12]]]
    return "\n".join(lines)


def _s(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def find(text: str, frag: str, start: int = 0) -> int:
    """단어 경계로 글자 그대로 찾기 (-1 = 없음)."""
    if not frag:
        return -1
    m = re.compile(r"(?<![A-Za-z0-9'])" + re.escape(frag) + r"(?![A-Za-z0-9'])").search(text, start)
    return m.start() if m else -1


def spans(text: str, frags: list[str]) -> list[tuple[int, int]]:
    """겹치지 않는 표시 위치 (앞에서부터)."""
    out, used = [], []
    for f in frags:
        i = find(text, f)
        while i >= 0 and any(a < i + len(f) and i < b for a, b in used):
            i = find(text, f, i + 1)
        if i >= 0:
            used.append((i, i + len(f)))
            out.append((i, i + len(f)))
    return sorted(out)


def fallback_chunks(sent: str) -> list[str]:
    """AI 덩어리가 틀렸을 때: 쉼표·접속사·전치사 앞에서 대충 자르기."""
    ws = sent.split()
    out, cur = [], []
    cut_before = {"and", "but", "or", "that", "which", "who", "when", "where", "because", "to", "in", "on", "at", "for", "with", "from", "of", "by"}
    for w in ws:
        if cur and (w.lower() in cut_before or len(cur) >= 3):
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
        if w.endswith(","):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def clean(raw, sents: list[tuple[int, str]], seed: int) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """AI 답 → (문장별 재료, 어색한 곳, 다시 요청할 문제, 경고)."""
    raw = raw if isinstance(raw, dict) else {}
    by_n = {}
    for i, r in enumerate(raw.get("sentences") or []):
        if isinstance(r, dict):
            try:
                by_n.setdefault(int(r.get("n") or 0), r)
            except (TypeError, ValueError):
                pass
    rows, errs, warns = [], [], []
    rng = random.Random(seed)
    for n, en in sents:
        r = by_n.get(n) or {}
        ko = _s(r.get("ko"))
        if not ko:
            errs.append(f"{n}번 문장의 해석(ko)이 없습니다.")
        en_bl = [x for x in (_s(b) for b in (r.get("en_blanks") or [])[:4]) if x and find(en, x) >= 0]
        ko_bl = [x for x in (_s(b) for b in (r.get("ko_blanks") or [])[:4]) if x and x in ko]
        verbs = []
        for v in r.get("verbs") or []:
            if isinstance(v, dict) and _s(v.get("text")) and find(en, _s(v["text"])) >= 0 and _s(v.get("base")):
                verbs.append({"text": _s(v["text"]), "base": _s(v["base"]).strip("()")})
        gram = []
        for g in r.get("grammar") or []:
            if isinstance(g, dict) and _s(g.get("text")) and find(en, _s(g["text"])) >= 0 and _s(g.get("wrong")) and _s(g["wrong"]) != _s(g["text"]):
                gram.append({"text": _s(g["text"]), "wrong": _s(g["wrong"]), "flip": rng.random() < 0.5})
        chunks = [_s(c) for c in (r.get("chunks") or []) if _s(c)]
        if not chunks or _norm(" ".join(chunks)) != _norm(en):
            if r:
                warns.append(f"{n}번 문장 배열 덩어리를 원문과 맞추지 못해 자동으로 잘랐어요.")
            chunks = fallback_chunks(en)
        shuffled = chunks[:]
        for _ in range(10):
            rng.shuffle(shuffled)
            if len(shuffled) < 2 or shuffled != chunks:
                break
        keys = [_s(k) for k in (r.get("keywords") or []) if _s(k)][:14]
        if not en_bl and r:
            warns.append(f"{n}번 문장의 영문 빈칸을 원문에서 찾지 못했어요.")
        rows.append({"n": n, "en": en, "ko": ko, "en_blanks": en_bl, "ko_blanks": ko_bl, "verbs": verbs, "grammar": gram,
                     "chunks": chunks, "shuffled": shuffled, "keywords": keys})
    odd = []
    for o in raw.get("odd") or []:
        if not isinstance(o, dict):
            continue
        t, w = _s(o.get("text")), _s(o.get("wrong"))
        try:
            n = int(o.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        row = next((x for x in rows if x["n"] == n), None) or next((x for x in rows if find(x["en"], t) >= 0), None)
        if t and w and w.lower() != t.lower() and row and find(row["en"], t) >= 0 and not any(x["n"] == row["n"] for x in odd):
            odd.append({"n": row["n"], "text": t, "wrong": w})
    return rows, odd[:3], errs, warns


# ---------------------------------------------------------------- 화면·PDF 조각

def marked(text: str, frags: list[str]) -> list[dict]:
    """글 → [{t, hit}] (frags 위치는 hit=True)."""
    out, cur = [], 0
    for a, b in spans(text, frags):
        if a > cur:
            out.append({"t": text[cur:a], "hit": False})
        out.append({"t": text[a:b], "hit": True})
        cur = b
    if cur < len(text):
        out.append({"t": text[cur:], "hit": False})
    return out


def blanked_ko(ko: str, frags: list[str]) -> list[dict]:
    out, cur = [], 0
    for f in frags:
        i = ko.find(f, cur)
        if i < 0:
            continue
        if i > cur:
            out.append({"t": ko[cur:i], "hit": False})
        out.append({"t": f, "hit": True})
        cur = i + len(f)
    if cur < len(ko):
        out.append({"t": ko[cur:], "hit": False})
    return out


def verb_line(en: str, verbs: list[dict]) -> list[dict]:
    """동사형: 동사 부분 → (원형)."""
    by = {v["text"]: v["base"] for v in verbs}
    return [{"t": f"({by[p['t']]})" if p["hit"] else p["t"], "hit": p["hit"]} for p in marked(en, list(by))]


def grammar_line(en: str, gram: list[dict], start_no: int | None = None) -> list[dict]:
    """어법 선택: 그 부분 → [맞는 것 / 틀린 것] (순서는 flip). start_no가 있으면 번호를 붙임 (Check용)."""
    by = {g["text"]: g for g in gram}
    out, k = [], start_no
    for p in marked(en, list(by)):
        if p["hit"]:
            g = by[p["t"]]
            pair = [g["wrong"], p["t"]] if g.get("flip") else [p["t"], g["wrong"]]
            out.append({"t": f"[{pair[0]} / {pair[1]}]", "hit": True, "no": k, "ans": p["t"]})
            if k is not None:
                k += 1
        else:
            out.append(p)
    return out


def odd_paragraph(rows: list[dict], odd: list[dict]) -> str:
    """어색한 곳: 문단을 이어 붙이되 odd 낱말을 틀린 말로 바꿈."""
    by_n = {o["n"]: o for o in odd}
    parts = []
    for r in rows:
        en = r["en"]
        o = by_n.get(r["n"])
        if o:
            i = find(en, o["text"])
            if i >= 0:
                en = en[:i] + o["wrong"] + en[i + len(o["text"]):]
        parts.append(en)
    return " ".join(parts)


def word_count(en: str) -> int:
    return len(en.split())


def split(text: str) -> list[str]:
    return split_sentences(text)


def check_line(row: dict, odd: dict | None, no: int, answers: list[str]) -> tuple[list[dict], int]:
    """Check: 한 문장에 어법 선택·어휘 선택·빈칸을 겹치지 않게 넣고 번호를 붙임. answers에 정답을 차례로 더함."""
    en = row["en"]
    marks = []  # (frag, kind, data)
    for g in row["grammar"]:
        marks.append((g["text"], "g", g))
    if odd:
        marks.append((odd["text"], "v", odd))
    if row["en_blanks"]:
        marks.append((row["en_blanks"][0], "b", None))
    placed, used = [], []
    for frag, kind, data in marks:
        i = find(en, frag)
        while i >= 0 and any(a < i + len(frag) and i < b for a, b in used):
            i = find(en, frag, i + 1)
        if i >= 0:
            used.append((i, i + len(frag)))
            placed.append((i, i + len(frag), kind, data))
    placed.sort()
    out, cur = [], 0
    for a, b, kind, data in placed:
        if a > cur:
            out.append({"t": en[cur:a], "hit": False})
        frag = en[a:b]
        if kind == "b":
            out.append({"t": "_" * max(8, len(frag)), "hit": True, "no": no, "blank": True})
        else:
            wrong = data["wrong"]
            pair = [wrong, frag] if (data.get("flip") if kind == "g" else (no % 2 == 0)) else [frag, wrong]
            out.append({"t": f"[{pair[0]} / {pair[1]}]", "hit": True, "no": no})
        answers.append(frag)
        no += 1
        cur = b
    if cur < len(en):
        out.append({"t": en[cur:], "hit": False})
    return out, no
