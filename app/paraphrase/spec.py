"""지문 변형 규칙.

AI는 변형한 지문(과 바꾼 이유 몇 줄)만 줍니다. 변형도(%)와 원문 대조는 코드가 계산하므로
선생님이 변형 지문을 직접 고쳐도 대조·변형도가 그대로 다시 맞춰집니다.
"""
import difflib
import re

from ..analysis.spec import split_sentences

STRENGTHS = {
    "light": {"name": "하", "band": (10, 30), "target": 20,
              "rule": "주로 어휘를 바꿉니다. 문장마다 핵심 단어·어구 1~3군데를 문맥에 맞는 동의어·유사 표현으로 교체하고, 문장 구조·순서·문장 수는 그대로 둡니다. 나머지 단어는 원문 그대로 둡니다."},
    "medium": {"name": "중", "band": (30, 60), "target": 45,
               "rule": "어휘 교체에 더해, 절반 정도의 문장은 구조를 바꿉니다 (능동↔수동, 분사구문, 관계사절, 명사구↔절, 연결어 교체 등). 나머지 문장은 어휘만 조금 바꿉니다. 문장 순서와 문장 수는 유지하고, 원문 단어도 절반가량은 그대로 남깁니다."},
    "strong": {"name": "상", "band": (60, 90), "target": 75,
               "rule": "내용과 논리 흐름은 같게 두되, 대부분의 문장을 다른 표현과 구조로 다시 씁니다. 두 문장을 합치거나 나눠도 됩니다. 핵심 내용어 일부만 원문 그대로 둡니다."},
}
MAX_PASSAGES = 30
_TOKEN = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def diff(original: str, changed: str) -> tuple[list[dict], int]:
    """단어 단위 대조 → ([{"op": equal|delete|insert, "text"}], 변형도%)."""
    a, b = tokens(original), tokens(changed)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = []

    def join(ws):
        s = ""
        for w in ws:
            s += w if (not s or re.match(r"[^\sA-Za-z0-9'(\[\"“]", w) or s.endswith(("(", "[", "“", '"'))) else " " + w
        return s

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": join(a[i1:i2])})
        else:
            if i2 > i1:
                ops.append({"op": "delete", "text": join(a[i1:i2])})
            if j2 > j1:
                ops.append({"op": "insert", "text": join(b[j1:j2])})
    for k, o in enumerate(ops):  # 문장부호 앞에는 띄우지 않음
        o["sp"] = k > 0 and not re.match(r"[.,;:!?)\]'’”]", o["text"])
    words_a = [w for w in a if re.match(r"[A-Za-z0-9]", w)]
    words_b = [w for w in b if re.match(r"[A-Za-z0-9]", w)]
    sm_w = difflib.SequenceMatcher(a=[w.lower() for w in words_a], b=[w.lower() for w in words_b], autojunk=False)
    ratio = round(100 * (1 - sm_w.ratio()))
    return ops, ratio


def change_list(ops: list[dict]) -> list[dict]:
    """대조 결과에서 '원문 → 변형' 쌍 목록 (교사용 표)."""
    out, i = [], 0
    while i < len(ops):
        o = ops[i]
        if o["op"] == "delete" and i + 1 < len(ops) and ops[i + 1]["op"] == "insert":
            out.append({"from": o["text"], "to": ops[i + 1]["text"]})
            i += 2
            continue
        if o["op"] == "delete":
            out.append({"from": o["text"], "to": "(삭제)"})
        elif o["op"] == "insert":
            out.append({"from": "(추가)", "to": o["text"]})
        i += 1
    return [c for c in out if re.search(r"[A-Za-z]", c["from"] + c["to"])]


def check(original: str, changed: str, strength: str) -> tuple[list[str], list[str], int]:
    """(다시 받을 사유, 경고, 변형도)."""
    errs, warns = [], []
    if not changed.strip():
        return ["변형 지문이 비어 있습니다."], [], 0
    _, ratio = diff(original, changed)
    lo, hi = STRENGTHS[strength]["band"]
    if ratio < lo:
        errs.append(f"변형도가 {ratio}%로 너무 낮습니다. {lo}~{hi}%가 되도록 더 바꿔 주세요.")
    elif ratio > hi:
        errs.append(f"변형도가 {ratio}%로 너무 높습니다. {lo}~{hi}%가 되도록 원문 표현을 더 살려 주세요.")
    n_a, n_b = len(split_sentences(original)), len(split_sentences(changed))
    if strength != "strong" and abs(n_a - n_b) > 1:
        errs.append(f"문장 수가 {n_a}개에서 {n_b}개로 바뀌었습니다. 문장 수를 유지해 주세요.")
    if len(changed) > len(original) * 1.6 or len(changed) < len(original) * 0.5:
        warns.append("변형 지문 길이가 원문과 많이 다릅니다.")
    return errs, warns, ratio


SYSTEM_PROMPT = """당신은 한국 고등학교 영어 내신 시험 출제 교사입니다. 학교 시험처럼 교과서·모의고사 원문을 변형합니다.
- 원문의 내용, 논리 흐름, 핵심 주장은 반드시 그대로 유지합니다. 사실을 바꾸거나 새 정보를 넣지 않습니다.
- 고등학생 수준의 자연스럽고 문법적으로 정확한 영어로 씁니다.
- 학생이 원문을 외워서는 풀 수 없도록, 하지만 원문을 이해했다면 알아볼 수 있도록 바꿉니다.
JSON 객체 하나로만 답합니다: {"text": "변형한 지문 전체", "notes": ["주요 변형 포인트 한 줄씩, 한국어, 3~6개"]}"""


def prompt(original: str, strength: str, level: str, focus: str, fix: list[str] | None = None) -> str:
    st = STRENGTHS[strength]
    lo, hi = st["band"]
    lines = [f"대상: {level or '고등학생'}", f"변형 강도: {st['name']} — {st['rule']}",
             f"변형도 목표: 원문 대비 단어 기준 약 {st['target']}% (반드시 {lo}~{hi}% 안). 변형도 = 원문과 달라진 단어의 비율입니다."]
    if focus:
        lines.append(f"특히 신경 쓸 것: {focus}")
    if fix:
        lines.append("직전 답의 문제점 (고쳐서 다시):\n- " + "\n- ".join(fix))
    lines.append("\n원문:\n" + original.strip())
    return "\n".join(lines)


def fit_ratio(original: str, changed: str, strength: str) -> tuple[str, int]:
    """변형도가 범위보다 높으면 문장 몇 개를 원문으로 되돌려 범위 안(가능하면 목표 근처)으로 맞춤.
    문장 수가 원문과 같을 때만 (문장끼리 짝이 맞을 때). → (지문, 변형도)"""
    lo, hi = STRENGTHS[strength]["band"]
    target = STRENGTHS[strength]["target"]
    _, ratio = diff(original, changed)
    if ratio <= hi:
        return changed, ratio
    sa, sb = split_sentences(original), split_sentences(changed)
    if len(sa) != len(sb) or len(sa) < 2:
        return changed, ratio
    cur = list(sb)
    for _ in range(len(sa)):
        best = None
        for i in range(len(cur)):
            if cur[i] == sa[i]:
                continue
            trial = cur[:i] + [sa[i]] + cur[i + 1:]
            _, r = diff(original, " ".join(trial))
            if r < lo:
                continue
            score = abs(r - target)
            if best is None or score < best[0]:
                best = (score, i, r)
        if best is None:
            break
        cur[best[1]] = sa[best[1]]
        ratio = best[2]
        if ratio <= hi and abs(ratio - target) <= 8:
            break
    return " ".join(cur), ratio


_END = re.compile(r'([.!?]["”’)]*)(\s+)(?=["“‘(]?[A-Z0-9])')


def annotated(ops: list[dict]) -> list[dict]:
    """교사용 대조: 변형 지문을 문장마다 나눠, 바뀐 부분은 번호 붙여 밑줄, 그 문장 아래에 원래 표현.
    → [{"segs": [{"t", "n", "sp"}], "orig": [{"n", "t"}]}]  (n 없는 seg는 그대로인 부분, t 빈 seg는 지워진 자리 표시)"""
    sents = [{"segs": [], "orig": []}]
    n = 0

    def add(text, num, sp):
        last = sents[-1]["segs"][-1]["t"] if sents[-1]["segs"] else ""
        if last and re.search(r'[.!?]["”’)]*$', last) and re.match(r'["“‘(]?[A-Z0-9]', text or ""):
            sents.append({"segs": [], "orig": []})  # 앞 조각이 문장 끝이면 새 문장
            sp = False
        # 그대로인 부분만 문장 경계에서 나눔 (바뀐 부분은 한 덩어리로)
        if num is None:
            parts = _END.split(text)
            # parts: [앞, 끝문장부호, 공백, 다음, ...]
            buf = parts[0]
            k = 1
            first = True
            while k < len(parts):
                buf += parts[k]
                sents[-1]["segs"].append({"t": buf, "n": None, "sp": sp if first else False})
                sents.append({"segs": [], "orig": []})
                first = False
                buf = parts[k + 2] if k + 2 < len(parts) else ""
                k += 3
            if buf:
                sents[-1]["segs"].append({"t": buf, "n": None, "sp": sp if first else False})
        else:
            sents[-1]["segs"].append({"t": text, "n": num, "sp": sp})

    i = 0
    while i < len(ops):
        o = ops[i]
        if o["op"] == "equal":
            add(o["text"], None, o.get("sp"))
            i += 1
            continue
        n += 1
        if o["op"] == "delete" and i + 1 < len(ops) and ops[i + 1]["op"] == "insert":
            add(ops[i + 1]["text"], n, o.get("sp"))
            sents[-1]["orig"].append({"n": n, "t": o["text"]})
            i += 2
        elif o["op"] == "delete":
            add("", n, o.get("sp"))
            sents[-1]["orig"].append({"n": n, "t": o["text"]})
            i += 1
        else:
            add(o["text"], n, o.get("sp"))
            sents[-1]["orig"].append({"n": n, "t": "(새로 넣은 말)"})
            i += 1
    return [s for s in sents if any(g["t"] or g["n"] for g in s["segs"])]
