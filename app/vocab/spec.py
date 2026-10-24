"""단어장 규칙: AI 프롬프트, 붙여 넣은 목록 읽기, 퀴즈 만들기·채점."""
import random
import re

MAX_WORDS = 200
CHUNK = 25  # AI에 한 번에 채워 달라고 하는 단어 수 (많으면 답이 잘려서 나눔)
FIELDS = ("word", "pos", "meaning", "syn", "ant", "example", "example_ko")

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
