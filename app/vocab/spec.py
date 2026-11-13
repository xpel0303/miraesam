"""단어장 규칙: AI 프롬프트, 붙여 넣은 목록 읽기, 퀴즈 만들기·채점."""
import random
import re

MAX_WORDS = 3000  # 사실상 제한 없음 (실수로 너무 큰 입력만 막는 안전장치)
MAX_PASSAGES = 100
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
    "exam": "실전형: 객관식(뜻·유의어·단어 고르기) + 주관식(예문 빈칸 쓰기)",
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


def sources(words: list[dict]) -> list[str]:
    """단어장에 들어 있는 지문 이름(나온 순서대로). 지문 표시가 없는 단어는 ''."""
    out: list[str] = []
    for w in words:
        s = w.get("src") or ""
        if w.get("word") and s not in out:
            out.append(s)
    return out


def by_source(words: list[dict]) -> list[dict]:
    """지문별로 묶기 → [{src, words}] (지문 나온 순서대로)."""
    out: dict[str, list[dict]] = {}
    for w in words:
        if w.get("word"):
            out.setdefault(w.get("src") or "", []).append(w)
    return [{"src": k, "words": v} for k, v in out.items()]


LAYOUTS = {"together": "한 시험지에 지문별로 나눠서", "pages": "지문마다 따로 시험지 (쪽마다 이름·점수)", "mixed": "지문 구분 없이 섞어서"}


def test_items(words: list[dict], mode: str, count: int, shuffle: bool, seed: int, by_src: bool,
               srcs: list[str] | None = None, per_src: bool = False) -> list[dict]:
    """시험지 → [{src, qs[{kind, mode, show, ko, choices, answer, ans_no}]}]. 지문별이면 지문 순서대로 묶고, 그 안에서 섞음.
    srcs: 출제할 지문만 (None이면 전부). per_src: count를 지문마다 문항 수로 씀."""
    rng = random.Random(seed)
    pool = [w for w in words if w.get("word") and w.get("meaning")]
    if srcs is not None:
        keep = set(srcs)
        pool = [w for w in pool if (w.get("src") or "") in keep]
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
        if per_src and by_src:
            n = min(count, len(ws))
        else:
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

# ---------------------------------------------------------------- 실전형 (객관식 + 주관식 빈칸)

EXAM_STEMS = {
    "meaning": "다음 단어의 뜻으로 알맞은 것은?",
    "syn": "다음 단어와 유의어(비슷한 말) 관계인 것은?",
    "word": "다음 뜻에 해당하는 단어로 알맞은 것은?",
}


def exam_blank(w: dict) -> tuple[str, str] | None:
    """예문 빈칸: 단어 원형 부분만 비우고 붙은 끝(-s, -d, -ing…)은 남김 → (빈칸 예문, 정답 원형)."""
    got = blank_sentence(w)
    if not got:
        return None
    sent, form = got
    word = w["word"].strip()
    tail = form[len(word):] if form.lower().startswith(word.lower()) else ""
    if tail and not re.fullmatch(r"[a-z]{1,4}", tail):
        tail = ""
    return sent.replace("__________", "____________" + tail, 1), (word if tail or form.lower() == word.lower() else form)


def _syn(w: dict) -> str:
    return next((x.strip() for x in re.split(r"[,;/]", w.get("syn") or "") if x.strip() and " " not in x.strip()), "")


def _four(rng, right: str, pool: list[str], avoid: set[str] = frozenset()) -> tuple[list[str], int]:
    others = [x for x in dict.fromkeys(pool) if x and x.lower() != right.lower() and x.lower() not in avoid]
    rng.shuffle(others)
    opts = others[:3] + [right]
    rng.shuffle(opts)
    return opts, opts.index(right) + 1


def _split(total: int, sizes: list[int]) -> list[int]:
    """total개를 그룹 크기에 비례해 나눔 (합 = total, 그룹마다 크기 넘지 않게)."""
    whole = sum(sizes) or 1
    out = [min(s, int(total * s / whole)) for s in sizes]
    i = 0
    while sum(out) < min(total, whole) and i < 10000:
        j = i % len(sizes)
        if out[j] < sizes[j]:
            out[j] += 1
        i += 1
    return out


def exam_items(words: list[dict], n_mc: int, n_sa: int, shuffle: bool, seed: int, by_src: bool,
               srcs: list[str] | None = None, per_src: bool = False) -> list[dict]:
    """실전형 → [{src, mc[{stem, show, choices, ans_no, answer}], sa[{sent, ko, answer}]}].
    n_mc·n_sa: 객관식·주관식 문항 수 (per_src면 지문마다, 아니면 전체를 지문 크기대로 나눔).
    주관식은 예문 빈칸이 되는 단어만, 한 단어는 한 번만 나옴."""
    rng = random.Random(seed)
    pool = [w for w in words if w.get("word") and w.get("meaning")]
    if srcs is not None:
        pool = [w for w in pool if (w.get("src") or "") in set(srcs)]
    groups: dict[str, list[dict]] = {}
    for w in pool:
        groups.setdefault((w.get("src") or "") if by_src else "", []).append(w)
    meanings = [x["meaning"] for x in pool]
    words_en = [x["word"] for x in pool]
    keys = list(groups)

    def fit(mc_n: int, sa_n: int, avail: int, can_sa: int) -> tuple[int, int]:
        """단어가 모자라면 객관식·주관식을 정한 비율대로 줄임 (주관식은 예문 빈칸이 되는 단어 수까지)."""
        sa_n = min(sa_n, can_sa)
        if mc_n + sa_n > avail and mc_n + sa_n:
            total = avail
            sa_new = min(sa_n, round(total * sa_n / (mc_n + sa_n)))
            if mc_n and total > 1 and sa_new >= total:
                sa_new = total - 1
            sa_n, mc_n = sa_new, min(mc_n, total - sa_new)
        return mc_n, sa_n

    if not (per_src and by_src):
        n_mc, n_sa = fit(n_mc, n_sa, len(pool), len([w for w in pool if exam_blank(w)]))
        sa_split = _split(n_sa, [len([w for w in groups[g] if exam_blank(w)]) for g in keys])
        mc_split = _split(n_mc, [len(groups[g]) - sa_split[i] for i, g in enumerate(keys)])
    out, k = [], 0
    for gi, src in enumerate(keys):
        ws = groups[src]
        ws = rng.sample(ws, len(ws)) if shuffle else list(ws)
        if per_src and by_src:
            want_mc, want_sa = fit(n_mc, n_sa, len(ws), len([w for w in ws if exam_blank(w)]))
        else:
            want_sa, want_mc = sa_split[gi], mc_split[gi]
        sa_w = [w for w in ws if exam_blank(w)][:want_sa]
        rest = [w for w in ws if w not in sa_w][:want_mc]
        mc = []
        for w in rest:
            kinds = ["meaning", "syn", "word"]
            kind = kinds[k % 3]
            k += 1
            syn = _syn(w)
            if kind == "syn" and not syn:
                kind = "meaning"
            if kind == "meaning":
                opts, no = _four(rng, w["meaning"], meanings)
                mc.append({"stem": EXAM_STEMS[kind], "show": w["word"], "choices": opts, "ans_no": no, "answer": w["meaning"]})
            elif kind == "syn":
                avoid = {x.strip().lower() for x in re.split(r"[,;/]", w.get("syn") or "")} | {w["word"].lower()}
                opts, no = _four(rng, syn, words_en + [_syn(x) for x in pool], avoid)
                mc.append({"stem": EXAM_STEMS[kind], "show": w["word"], "choices": opts, "ans_no": no, "answer": syn})
            else:
                opts, no = _four(rng, w["word"], words_en)
                mc.append({"stem": EXAM_STEMS[kind], "show": w["meaning"], "choices": opts, "ans_no": no, "answer": w["word"]})
        sa = []
        for w in sa_w:
            sent, ans = exam_blank(w)
            sa.append({"sent": sent, "ko": w.get("example_ko", ""), "answer": ans})
        if mc or sa:
            out.append({"src": src, "mc": mc, "sa": sa})
    return out
