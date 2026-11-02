"""단어장 규칙: AI 프롬프트, 붙여 넣은 목록 읽기, 퀴즈 만들기·채점."""
import random
import re

MAX_WORDS = 200
MAX_PASSAGES = 30
SEP = "\n\n[[지문]]\n\n"  # 여러 지문을 source_text 한 칸에 저장할 때 구분


def join_passages(ps: list[dict]) -> str:
    return SEP.join(f"{p.get('title', '')}\n{p['text']}" for p in ps)


def split_passages(text: str) -> list[dict]:
    if "[[지문]]" not in (text or ""):
        return [{"title": "", "text": text or ""}] if (text or "").strip() else []
    out = []
    for part in text.split(SEP):
        title, _, body = part.partition("\n")
        if body.strip():
            out.append({"title": title.strip(), "text": body.strip()})
    return out
CHUNK = 25  # AI에 한 번에 채워 달라고 하는 단어 수 (많으면 답이 잘려서 나눔)
FIELDS = ("word", "pos", "meaning", "syn", "ant", "example", "example_ko", "src")  # src: 어느 지문에서 뽑았는지

QUIZ_MODES = {
    "mc_en": "영어 보고 뜻 고르기 (객관식)",
    "mc_ko": "뜻 보고 영어 고르기 (객관식)",
    "write_ko": "영어 보고 뜻 쓰기",
    "write_en": "뜻 보고 영어 쓰기",
}


# ---------------------------------------------------------------- 붙여 넣은 목록

def parse_list(text: str) -> list[dict]:
    """한 줄에 하나: 'word' / 'word | 뜻' / 'word<TAB>뜻' / 'word - 뜻' / 'word: 뜻' / 'word, 뜻'(쉼표 뒤가 한글). 중복 단어는 하나만."""
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip().lstrip("-•*0123456789.) ").strip()
        if not line:
            continue
        m = re.split(r"\s*\|\s*|\t+|\s+[-–—:]\s+|:\s+|\s*,\s*(?=[(~\[]?[가-힣])", line, maxsplit=1)
        word = m[0].strip()
        meaning = m[1].strip() if len(m) > 1 else ""
        if not re.search(r"[A-Za-z]", word) or len(word) > 60:
            continue
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"word": word, "meaning": meaning})
    return out[:MAX_WORDS]


def clean_entry(x: dict) -> dict:
    e = {}
    for f in FIELDS:
        v = x.get(f, "")
        if isinstance(v, list):
            v = ", ".join(str(i) for i in v if str(i).strip())
        e[f] = re.sub(r"\s+", " ", str(v or "")).strip()
    return e


def blank_needed(e: dict) -> bool:
    return not (e.get("meaning") and e.get("pos") and e.get("example"))


# ---------------------------------------------------------------- 프롬프트

SYSTEM_PROMPT = """당신은 한국 고등학생용 영어 단어장을 만드는 교사입니다. JSON으로만 답합니다.
각 단어 항목:
- word: 표제어 (지문에 나온 변형이면 원형으로. 예: affected → affect. 숙어는 그대로)
- pos: 품사 약어 하나 (n. v. adj. adv. prep. conj. phr.)
- meaning: 지문(또는 일반적) 문맥에 맞는 대표 뜻 1~3개, 쉼표로 구분, 한국어
- syn: 유의어 1~3개, 쉼표 (없으면 빈 문자열)
- ant: 반의어 1~2개 (없으면 빈 문자열)
- example: 예문 한 문장. 지문이 있으면 지문 속 문장을 그대로, 없으면 고등학생 수준의 짧은 문장
- example_ko: 예문 해석
출력: {"words": [ {...}, ... ]}"""


def extract_prompt(passage: str, count: int, level: str) -> str:
    return (
        f"대상: {level or '고등학생'}\n"
        f"아래 지문에서 학습 가치가 높은 단어·숙어를 정확히 {count}개 고르세요. "
        "고유명사, 너무 쉬운 단어(the, make, good 수준)는 빼고, 시험에 나올 만한 순서로 정렬합니다. "
        "example은 반드시 지문 속 문장에서 가져오세요.\n\n지문:\n" + passage.strip()
    )


def fill_prompt(entries: list[dict], passage: str = "", level: str = "") -> str:
    lines = [f"- {e['word']}" + (f" (선생님이 정한 뜻: {e['meaning']} — 이 뜻을 그대로 쓰세요)" if e.get("meaning") else "") for e in entries]
    ctx = f"\n\n참고 지문 (예문은 여기서 찾으세요):\n{passage.strip()}" if passage.strip() else ""
    return f"대상: {level or '고등학생'}\n아래 단어 {len(entries)}개의 항목을 모두, 같은 순서로 채우세요.\n" + "\n".join(lines) + ctx


# ---------------------------------------------------------------- 퀴즈

def _norm(s: str) -> str:
    return re.sub(r"[\s~()\[\].·,]", "", s.lower())


def meanings_of(e: dict) -> list[str]:
    return [m.strip() for m in re.split(r"[,;/]", e.get("meaning", "")) if m.strip()]


def build_quiz(words: list[dict], mode: str, count: int, seed: int) -> list[dict]:
    """문항 목록. 정답은 서버에만 두고, 화면에는 prompt와 choices만 보냅니다."""
    rng = random.Random(seed)
    pool = [w for w in words if w.get("word") and w.get("meaning")]
    picked = rng.sample(pool, min(count, len(pool)))
    qs = []
    for w in picked:
        if mode in ("mc_en", "write_ko"):
            q = {"idx": words.index(w), "prompt": w["word"], "answer": w["meaning"]}
        else:
            q = {"idx": words.index(w), "prompt": w["meaning"], "answer": w["word"]}
        if mode.startswith("mc"):
            key = "meaning" if mode == "mc_en" else "word"
            others = list({o[key] for o in pool if o is not w and o[key] != w[key]})
            rng.shuffle(others)
            choices = others[:3] + [w[key]]
            rng.shuffle(choices)
            q["choices"] = choices
        qs.append(q)
    return qs


def grade(words: list[dict], mode: str, idx: int, given: str) -> bool:
    w = words[idx]
    given = (given or "").strip()
    if not given:
        return False
    if mode == "mc_en":
        return given == w["meaning"]
    if mode == "mc_ko":
        return given == w["word"]
    if mode == "write_en":
        return _norm(given) == _norm(w["word"])
    # write_ko: 여러 뜻 중 하나만 맞으면 정답 (띄어쓰기·물결표 무시)
    g = _norm(given)
    for m in meanings_of(w):
        m = _norm(m)
        if g == m or (len(m) >= 2 and m in g) or (len(g) >= 3 and g in m):
            return True
    return False


# ---------------------------------------------------------------- 종이 시험지

TEST_MODES = {
    "en_ko": "영어 → 뜻 쓰기",
    "ko_en": "뜻 → 영어 쓰기",
    "mix": "쓰기 (영어·뜻 섞어서)",
    "mc_en": "영어 → 뜻 고르기 (객관식)",
    "mc_ko": "뜻 → 영어 고르기 (객관식)",
    "sent": "예문 빈칸에 알맞은 단어 쓰기",
    "sent_mc": "예문 빈칸에 알맞은 단어 고르기 (객관식)",
    "all": "골고루 섞기 (쓰기·객관식·예문 빈칸)",
}
TEST_DIRS = {
    "en_ko": "영어 단어의 뜻을 쓰시오.",
    "ko_en": "우리말 뜻에 맞는 영어 단어를 쓰시오.",
    "mc_en": "영어 단어의 뜻으로 알맞은 것을 고르시오.",
    "mc_ko": "우리말 뜻에 맞는 영어 단어를 고르시오.",
    "sent": "빈칸에 들어갈 알맞은 단어를 쓰시오. (필요하면 형태를 바꿀 것)",
    "sent_mc": "빈칸에 들어갈 알맞은 단어를 고르시오.",
}
CIRCLED = "①②③④⑤"


def blank_sentence(w: dict) -> tuple[str, str] | None:
    """예문에서 그 단어(변형 포함)를 찾아 빈칸으로 → (빈칸 예문, 예문 속 형태). 못 찾으면 None."""
    ex, word = w.get("example") or "", (w.get("word") or "").strip()
    if not ex or not word:
        return None
    parts = word.split()
    stem = parts[0]
    if len(stem) > 4 and stem[-1] in "ey":
        stem = stem[:-1]
    pat = r"\b" + re.escape(stem) + r"[a-z]*" + "".join(r"\s+" + re.escape(x) + r"[a-z]*" for x in parts[1:]) + r"\b"
    m = re.search(pat, ex, re.I)
    if not m:
        return None
    return ex[: m.start()] + "__________" + ex[m.end():], m.group(0)


def _choices(rng, right: str, pool: list[str]) -> tuple[list[str], int]:
    others = [x for x in dict.fromkeys(pool) if x and x != right]
    rng.shuffle(others)
    opts = others[:4] + [right]
    rng.shuffle(opts)
    return opts, opts.index(right) + 1


def test_items(words: list[dict], mode: str, count: int, shuffle: bool, seed: int, by_src: bool) -> list[dict]:
    """시험지 → [{src, qs[{kind, mode, show, ko, choices, answer, ans_no}]}]. 지문별이면 지문 순서대로 묶고, 그 안에서 섞음."""
    rng = random.Random(seed)
    pool = [w for w in words if w.get("word") and w.get("meaning")]
    if mode in ("sent", "sent_mc"):
        pool = [w for w in pool if blank_sentence(w)]
    groups: dict[str, list[dict]] = {}
    for w in pool:
        groups.setdefault((w.get("src") or "") if by_src else "", []).append(w)
    total = sum(len(g) for g in groups.values()) or 1
    out = []
    cycle = ["en_ko", "mc_en", "ko_en", "mc_ko", "sent", "sent_mc"]
    k = 0
    for src, ws in groups.items():
        n = max(1, round(count * len(ws) / total)) if count < total else len(ws)
        picked = rng.sample(ws, min(n, len(ws))) if shuffle else ws[:n]
        qs = []
        for w in picked:
            m = mode
            if mode == "mix":
                m = rng.choice(["en_ko", "ko_en"])
            elif mode == "all":
                m = cycle[k % len(cycle)]
                k += 1
                if m.startswith("sent") and not blank_sentence(w):
                    m = "mc_en"
            q = {"mode": m, "show": "", "ko": "", "choices": [], "answer": "", "ans_no": 0}
            if m == "en_ko":
                q.update(kind="write", show=w["word"], answer=w["meaning"])
            elif m == "ko_en":
                q.update(kind="write", show=w["meaning"], answer=w["word"])
            elif m == "mc_en":
                opts, no = _choices(rng, w["meaning"], [x["meaning"] for x in pool])
                q.update(kind="mc", show=w["word"], choices=opts, ans_no=no, answer=w["meaning"])
            elif m == "mc_ko":
                opts, no = _choices(rng, w["word"], [x["word"] for x in pool])
                q.update(kind="mc", show=w["meaning"], choices=opts, ans_no=no, answer=w["word"])
            else:
                sent, form = blank_sentence(w)
                q.update(kind="sent", show=sent, ko=w.get("example_ko", ""), answer=form)
                if m == "sent_mc":
                    opts, no = _choices(rng, w["word"], [x["word"] for x in pool])
                    q.update(choices=opts, ans_no=no, answer=w["word"])
            qs.append(q)
        if qs:
            out.append({"src": src, "qs": qs})
    return out