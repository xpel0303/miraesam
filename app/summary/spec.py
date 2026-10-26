"""지문 정리 (지문 한쪽 정리) 규칙.

지문마다:
  1쪽  지문 및 해석(영어 | 자연스러운 우리말) → 지문 핵심 정리(제목·주제·목적·세 줄 요약) → 주요 어휘
  2쪽  한 줄 해석 (끊어 읽기 직독직해: 영어 덩어리 / 영어 어순 그대로 우리말)  ← 선택
문장 나누기는 코드가 하고, AI는 문장 번호마다 덩어리를 줍니다. 덩어리를 이으면 원래 문장과 글자가 같아야 합니다.
"""
import re

from ..analysis.spec import split_sentences

MAX_PASSAGES = 30
POS = ("명", "동", "형", "부")

SYSTEM_PROMPT = """당신은 한국 고등학생용 영어 '지문 정리본'을 만드는 교재 저자입니다. JSON 객체 하나로만 답합니다.
- 지문 내용에 없는 말을 지어내지 않습니다. 뜻은 이 지문에서 쓰인 뜻으로.
- 해석(translation)은 한국어답게 자연스럽게 한 문단으로. 한 줄 해석(sentences)은 끊어 읽기 직독직해로, 영어 어순 그대로."""


def prompt(text: str, sentences: list[str], level: str = "", fix: list[str] | None = None) -> str:
    lines = [f"대상: {level or '고등학생'}", "아래 영어 지문의 정리본 내용을 만들어 주세요.", "",
             "출력 형식:",
             "{",
             '  "title": "영어 제목 (Title Case, 짧게)",',
             '  "topic": "주제 — 한국어 한 줄, 명사형으로 끝냄 (예: …한다는 주제 / …의 중요성)",',
             '  "purpose": "목적 — 두 문장: \\"이 글은 ~하기 위한 것이다. 또한, ~하고자 한다.\\" (이야기 글이면 \\"이 글은 ~을 다루고 있다. …\\")",',
             '  "summary": ["세 줄 요약 1 (무엇)", "2 (어떻게·왜)", "3 (결과·시사점)"],',
             '  "translation": "지문 전체의 자연스러운 우리말 해석 (한 문단)",',
             '  "vocab": [{"word": "지문에 나온 형태 그대로", "pos": "명|동|형|부", "meaning": "이 지문에서의 뜻 (짧게)"}, ... 12~18개, 고등학생이 알아야 할 내용어],',
             '  "sentences": [{"n": 문장 번호, "en": ["영어 덩어리", ...], "ko": ["같은 순서의 우리말 덩어리", ...]}, ... 문장마다 하나]',
             "}",
             "",
             "한 줄 해석 규칙:",
             "- en: 문장을 의미 단위(2~6단어)로 자릅니다. 글자는 하나도 바꾸지 않아서, 덩어리를 띄어쓰기로 이으면 원래 문장과 똑같아야 합니다.",
             "  자르는 곳: 전치사구 앞, 관계사·that절 앞, to부정사 앞, 문두 부사·연결어 뒤(For example,), 분사구 앞뒤, 등위 and/but 앞.",
             "- ko: en 덩어리마다 하나씩, 영어 어순 그대로 읽히는 우리말 (예: 상호성은 / 사회적 규범이다 / 중요한 역할을 하는 / 인간 행동을 형성하는 데).",
             "  ko 개수는 en 개수와 같아야 합니다. 어색하면 조사·어미(…하는, …라는, …에게)를 붙여 읽히게.",
             "- 문장 끝 문장부호는 마지막 덩어리에 붙입니다. 문장을 빼거나 줄이지 않습니다.",
             "",
             "지문 (문장 번호):"]
    lines += [f"[{i}] {s}" for i, s in enumerate(sentences, 1)]
    if fix:
        lines += ["", "직전 답의 문제점 (고쳐서 다시):", *[f"- {x}" for x in fix[:12]]]
    return "\n".join(lines)


def _s(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _chunk(v) -> str:
    """덩어리 앞뒤에 AI가 붙인 / 를 떼어냄 (PDF에서 '/ /'로 겹쳐 보이지 않게)."""
    return _s(re.sub(r"^\s*/+|/+\s*$", "", str(v or "")))


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def clean(raw, sentences: list[str]) -> tuple[dict, list[str], list[str]]:
    """AI 답 → (정리 내용, 다시 요청할 문제, 경고). 덩어리가 원문과 다르면 그 문장은 원문 한 덩어리로 대신 넣음."""
    raw = raw if isinstance(raw, dict) else {}
    errs, warns = [], []
    vocab = []
    for v in raw.get("vocab") or []:
        if isinstance(v, dict) and _s(v.get("word")):
            pos = _s(v.get("pos"))[:1]
            vocab.append({"word": _s(v.get("word"))[:40], "pos": pos if pos in POS else "명", "meaning": _s(v.get("meaning"))[:40]})
    summary = [_s(x) for x in (raw.get("summary") or []) if _s(x)][:3]
    by_n = {}
    for i, r in enumerate(raw.get("sentences") or []):
        if isinstance(r, dict):
            try:
                n = int(r.get("n") or i + 1)
            except (TypeError, ValueError):
                n = i + 1
            by_n.setdefault(n, r)
    rows = []
    for n, sent in enumerate(sentences, 1):
        r = by_n.get(n) or {}
        en = [_chunk(c) for c in (r.get("en") or []) if _chunk(c)]
        ko = [_chunk(c) for c in (r.get("ko") or []) if _chunk(c)]
        ok = en and _norm(" ".join(en)) == _norm(sent) and len(en) == len(ko)
        if not ok:
            if not en:
                errs.append(f"{n}번 문장의 한 줄 해석이 없습니다.")
            elif _norm(" ".join(en)) != _norm(sent):
                errs.append(f"{n}번 문장: en 덩어리를 이으면 원래 문장과 달라요. 글자를 바꾸지 말고 자르기만 하세요. 원래 문장: {sent}")
            else:
                errs.append(f"{n}번 문장: en {len(en)}개, ko {len(ko)}개 — 개수를 맞추세요.")
        rows.append({"en": en if ok else [sent], "ko": ko if ok else [" ".join(ko) or "(해석 필요)"], "ok": bool(ok)})
    out = {"title": _s(raw.get("title"))[:120], "topic": _s(raw.get("topic"))[:200], "purpose": _s(raw.get("purpose"))[:400],
           "summary": summary, "translation": _s(raw.get("translation")), "vocab": vocab[:20], "sentences": rows}
    if not out["translation"]:
        errs.append("해석(translation)이 비었습니다.")
    if len(summary) != 3:
        errs.append("세 줄 요약은 정확히 3개여야 합니다.")
    if len(vocab) < 8:
        errs.append(f"주요 어휘가 {len(vocab)}개뿐입니다. 12~18개로.")
    for k, name in (("title", "제목"), ("topic", "주제"), ("purpose", "목적")):
        if not out[k]:
            errs.append(f"{name}이(가) 비었습니다.")
    bad = [i + 1 for i, r in enumerate(rows) if not r["ok"]]
    if bad:
        warns.append(f"{', '.join(map(str, bad[:10]))}번 문장은 끊어 읽기를 받지 못해 통째로 넣었어요. 직접 고칠 수 있어요.")
    return out, errs, warns


# ---------------------------------------------------------------- 선생님이 고친 글 → 정리 내용

def parse_vocab(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0]:
            cells += [""] * (3 - len(cells))
            pos = cells[1][:1] if cells[1][:1] in POS else "명"
            out.append({"word": cells[0][:40], "pos": pos, "meaning": cells[2][:40]})
    return out[:24]


def vocab_text(vocab: list[dict]) -> str:
    return "\n".join(f"{v['word']} | {v['pos']} | {v['meaning']}" for v in vocab)


def parse_lines(text: str) -> list[dict]:
    """한 줄에 한 문장: 영어 덩어리 / 영어 덩어리 || 우리말 / 우리말"""
    rows = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        en, _, ko = line.partition("||")
        en = [_chunk(c) for c in en.split(" / ") if _chunk(c)]
        ko = [_chunk(c) for c in ko.split(" / ") if _chunk(c)]
        if en:
            rows.append({"en": en, "ko": ko, "ok": len(en) == len(ko)})
    return rows


def lines_text(rows: list[dict]) -> str:
    return "\n".join(" / ".join(r["en"]) + " || " + " / ".join(r["ko"]) for r in rows)


def passage_text(p: dict) -> str:
    rows = (p.get("out") or {}).get("sentences") or []
    return " ".join(" ".join(r["en"]) for r in rows) or p.get("text", "")


def split(text: str) -> list[str]:
    return split_sentences(text)


def tidy(out: dict) -> dict:
    """예전에 만든 정리본도 PDF에서 덩어리 앞뒤 / 가 겹치지 않게."""
    rows = [{**r, "en": [_chunk(c) for c in r.get("en", []) if _chunk(c)], "ko": [_chunk(c) for c in r.get("ko", []) if _chunk(c)]}
            for r in out.get("sentences") or []]
    return {**out, "sentences": rows}
