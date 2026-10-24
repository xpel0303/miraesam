"""기출 분석 규칙: Gemini에 시험지 쪽 이미지를 2쪽씩 보내 문항을 글자 그대로 옮겨 받고,
합치기·번호 검사·유형 분류·통계는 코드가 합니다.

문항 하나 (questions[]):
  label   시험지에 적힌 번호 그대로 ("1", "서술형 1", "서답형 2")
  n       전체 순서 (객관식 → 서술형, 1부터)
  kind    mc | sa
  points  배점 (모르면 None)
  stem    발문 ("다음 글의 주제로 가장 적절한 것은?")
  group   지문을 같이 쓰는 문항 범위 ("1-2") — 지문은 그 범위의 첫 문항에만
  passage 지문 (밑줄은 [u]…[/u], ⓐ ① (A) 같은 기호는 그대로)
  box     <보기>·주어진 문장·요약문 등 따로 놓인 상자
  choices 선지 (객관식 5개)
  conditions 서술형 조건
  type    유형 (아래 규칙이 발문으로 정하고, 못 정하면 AI 판단)
  point   출제 포인트 한 줄 (어떤 어법·어휘·내용을 묻는지)
  pages   이 문항이 걸친 쪽 번호
"""
import re
import statistics

CHUNK = 2  # 한 번에 보내는 쪽 수
KEYS = ("label", "kind", "points", "stem", "group", "passage", "box", "choices", "conditions", "type", "point")

# 발문 → 유형 (위에서부터 먼저 맞는 것). 서술형은 따로.
RULES = [
    (r"흐름과\s*(관계\s*)?(없|무관)", "무관한 문장"),
    (r"주어진\s*(문장|글)이?\s*들어가|들어갈\s*위치", "삽입"),
    (r"(이어질|이어지는)\s*글의\s*순서|순서로\s*가장\s*적절|순서대로\s*배열", "순서"),
    (r"요약", "요약"),
    (r"어법", "어법"),
    (r"(낱말|단어|어휘)의?\s*쓰임|문맥상\s*(적절하지|쓰임)|영영\s*풀이|뜻풀이", "어휘"),
    (r"의미가?\s*(다른|같은)|관계가\s*(다른|같은)|짝지어진", "어휘"),
    (r"접속사|연결어|연결사|연결하는\s*말", "연결어"),
    (r"빈칸", "빈칸"),
    (r"의미하는\s*바|의미로|의미하는\s*내용|함축", "함축 의미"),
    (r"주제", "주제"),
    (r"제목", "제목"),
    (r"요지", "요지"),
    (r"주장", "주장"),
    (r"목적", "목적"),
    (r"심경|분위기|어조", "심경"),
    (r"가리키는\s*대상|지칭", "지칭"),
    (r"일치하(지\s*않|는)|내용과\s*(같|다른)|알\s*수\s*(없|있)|사실과", "내용일치"),
    (r"추론|추론한", "추론"),
    (r"사례|예시로|적용", "적용"),
    (r"대화", "대화"),
]
SA_RULES = [
    (r"어법|틀린\s*(곳|부분|것)|고쳐", "서술형(어법)"),
    (r"(본문|글)에서\s*찾아|단어를\s*찾아|영영\s*풀이", "서술형(어휘)"),
    (r"(한글|우리말)로\s*(서술|쓰|답|설명)|이유를|원인", "서술형(내용)"),
    (r"배열|영작|영어로", "서술형(영작)"),
    (r"요약", "서술형(요약)"),
    (r"해석", "서술형(해석)"),
    (r"조건|완성|빈칸", "서술형(영작)"),
]
TYPE_ORDER = ["주제", "제목", "요지", "주장", "목적", "심경", "함축 의미", "내용일치", "추론", "적용", "지칭", "대화", "어법", "어휘", "연결어",
              "빈칸", "순서", "삽입", "무관한 문장", "요약", "장문", "서술형(영작)", "서술형(어법)", "서술형(어휘)", "서술형(요약)",
              "서술형(내용)", "서술형(해석)", "서술형", "기타"]


def classify(stem: str, kind: str, ai_type: str = "") -> str:
    s = re.sub(r"\s+", " ", stem or "")
    for pat, t in (SA_RULES if kind == "sa" else RULES):
        if re.search(pat, s):
            return t
    ai = (ai_type or "").strip()
    if kind == "sa":
        return ai if ai.startswith("서술형") else "서술형"
    return ai[:20] or "기타"


def _s(v) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()


def _points(v):
    m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


def label_key(label: str) -> tuple[int, int]:
    """정렬용: 객관식 (0, 번호), 서술형 (1, 번호)."""
    sa = bool(re.search(r"서[술답]|주관|논술", label))
    m = re.search(r"\d+", label)
    return (1 if sa else 0, int(m.group()) if m else 999)


def norm_label(raw, kind: str) -> str:
    """'서답(서술)형7' → '서답형 7', '<서술형 1>' → '서술형 1', '7번' → '7'."""
    s = _s(raw).replace("번", "")
    s = re.sub(r"[\[\]<>〈〉]", "", s)
    s = re.sub(r"\((서술|서답|논술|주관)\)", "", s).strip()
    if re.fullmatch(r"\d+", s):
        return s if kind == "mc" else s  # 서술형인데 숫자만: 그대로 (객관식 번호에 이어 붙은 학교도 있음)
    m = re.search(r"(서술형|서답형|주관식|논술형)\s*(\d+)", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return s[:12]


def clean_item(x: dict, page_from: int) -> dict | None:
    if not isinstance(x, dict):
        return None
    kind = "sa" if str(x.get("kind", "")).lower() in ("sa", "서술형", "서답형", "subjective") else "mc"
    label = norm_label(x.get("label") or x.get("n"), kind)
    if not label:
        return None
    if re.search(r"서[술답]|주관|논술", label):
        kind = "sa"
    choices = [_s(c) for c in (x.get("choices") or []) if _s(c)]
    return {
        "label": label, "kind": kind, "points": _points(x.get("points")),
        "stem": _s(x.get("stem")), "group": _s(x.get("group")).replace("~", "-"),
        "passage": str(x.get("passage") or "").strip(), "box": str(x.get("box") or "").strip(),
        "choices": choices[:5] if kind == "mc" else choices, "conditions": str(x.get("conditions") or "").strip(),
        "type": _s(x.get("type")), "point": _s(x.get("point"))[:120],
        "cont": bool(x.get("continues_from_previous_page") or x.get("cont")), "pages": [page_from],
    }


def merge(chunks: dict) -> tuple[list[dict], dict]:
    """쪽 묶음별 답 → 문항 목록 (같은 번호가 두 묶음에 걸치면 이어 붙임), 머리말."""
    by_label: dict[str, dict] = {}
    meta: dict = {}
    for start in sorted(chunks, key=int):
        c = chunks[start] or {}
        if not meta and isinstance(c.get("header"), dict):
            meta = {k: _s(v) for k, v in c["header"].items() if _s(v)}
        page_map = c.get("_pages") or [int(start) + 1 + i for i in range(CHUNK)]
        for raw in c.get("items") or []:
            if not isinstance(raw, dict):
                continue
            k = int(raw["page"]) - 1 if str(raw.get("page", "")).isdigit() and 0 < int(raw["page"]) <= len(page_map) else 0
            it = clean_item(raw, page_map[k])
            if not it:
                continue
            old = by_label.get(it["label"])
            if old is None:
                by_label[it["label"]] = it
                continue
            # 쪽을 넘어 이어진 문항: 비어 있던 칸 채우고, 지문은 이어 붙임
            for k in ("stem", "group", "box", "conditions", "type", "point"):
                if not old[k] and it[k]:
                    old[k] = it[k]
            if it["passage"] and it["passage"] not in old["passage"]:
                old["passage"] = (old["passage"] + "\n" + it["passage"]).strip()
            if len(it["choices"]) > len(old["choices"]):
                old["choices"] = it["choices"]
            if old["points"] is None:
                old["points"] = it["points"]
            old["pages"] = sorted(set(old["pages"] + it["pages"]))
    qs = sorted(by_label.values(), key=lambda q: label_key(q["label"]))
    for i, q in enumerate(qs, 1):
        q["n"] = i
        q["ai_type"] = q["type"]
        q["type"] = classify(q["stem"], q["kind"], q["type"])
        q.pop("cont", None)
    _spread_groups(qs)
    return qs, meta


def _spread_groups(qs: list[dict]):
    """'[1~2]', '[7, <서답형1>]' 처럼 지문을 같이 쓰는 문항 묶음 → group 을 '1-2' / '7, 서답형 1' 로 정리.
    지문은 묶음의 첫 문항에만 두고, 나머지 문항은 shared_with 로 그 문항 번호를 가리킴."""
    labels = {q["label"] for q in qs}
    first: dict[str, str] = {}
    for q in qs:
        g = (q["group"] or "").strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", g)
        if m:
            g = f"{m.group(1)}-{m.group(2)}"
        elif g:
            parts = [norm_label(x, "mc") for x in re.split(r"\s*,\s*", g) if x.strip()]
            g = ", ".join(parts) if any(p in labels for p in parts) else ""
        q["group"] = g
        if g and words(q["passage"]) >= 20:
            first.setdefault(g, q["label"])
    for q in qs:
        q["shared_with"] = first.get(q["group"], "") if q["group"] and words(q["passage"]) < 20 else ""


def validate(qs: list[dict], n_pages: int) -> list[str]:
    warns = []
    mc = [q for q in qs if q["kind"] == "mc"]
    nums = sorted(int(q["label"]) for q in mc if q["label"].isdigit())
    if nums:
        missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
        if missing:
            warns.append(f"객관식 번호가 빠졌어요: {', '.join(map(str, missing[:10]))}번 (쪽 이미지를 보고 추가해 주세요)")
    bad = [q["label"] for q in mc if len(q["choices"]) != 5]
    if bad:
        warns.append(f"선지가 5개가 아닌 문항: {', '.join(bad[:10])}")
    nop = [q["label"] for q in qs if q["points"] is None]
    if nop:
        warns.append(f"배점을 못 읽은 문항: {', '.join(nop[:10])}")
    total = sum(q["points"] or 0 for q in qs)
    if qs and not nop and abs(total - 100) > 0.6:
        warns.append(f"배점 합계가 {round(total, 2)}점이에요. 잘못 읽은 배점이 있는지 확인해 주세요.")
    nostem = [q["label"] for q in qs if not q["stem"]]
    if nostem:
        warns.append(f"발문이 비어 있는 문항: {', '.join(nostem[:10])}")
    return warns


def check_counts(qs: list[dict], meta: dict) -> list[str]:
    """표지에 적힌 문항 수(선다형 21, 서답형 7)와 뽑은 개수 비교."""
    out = []
    for key, kind, name in (("mc_count", "mc", "객관식"), ("sa_count", "sa", "서술형")):
        m = re.search(r"\d+", str(meta.get(key) or ""))
        if m:
            want, got = int(m.group()), sum(1 for q in qs if q["kind"] == kind)
            if want != got:
                out.append(f"시험지 표지에는 {name} {want}문항인데 {got}문항을 찾았어요. 빠지거나 잘못 나뉜 문항이 있는지 확인해 주세요.")
    return out


# ---------------- 통계 (분석지) ----------------

def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", re.sub(r"\[/?u\]", "", text or "")))


def stats(qs: list[dict]) -> dict:
    total = round(sum(q["points"] or 0 for q in qs), 2)
    by_type: dict[str, dict] = {}
    for q in qs:
        t = by_type.setdefault(q["type"], {"type": q["type"], "count": 0, "points": 0.0, "labels": []})
        t["count"] += 1
        t["points"] += q["points"] or 0
        t["labels"].append(q["label"])
    types = sorted(by_type.values(), key=lambda t: (TYPE_ORDER.index(t["type"]) if t["type"] in TYPE_ORDER else 99, t["type"]))
    for t in types:
        t["points"] = round(t["points"], 2)
        t["share"] = round(100 * t["points"] / total) if total else 0
    mc = [q for q in qs if q["kind"] == "mc"]
    sa = [q for q in qs if q["kind"] == "sa"]
    passages = [q for q in qs if words(q["passage"]) >= 40]
    lens = [words(q["passage"]) for q in passages]
    pts = [q["points"] for q in qs if q["points"] is not None]
    hi = max(pts) if pts else None
    return {
        "total": total, "n": len(qs), "mc": len(mc), "sa": len(sa),
        "mc_points": round(sum(q["points"] or 0 for q in mc), 2), "sa_points": round(sum(q["points"] or 0 for q in sa), 2),
        "sa_share": round(100 * sum(q["points"] or 0 for q in sa) / total) if total else 0,
        "types": types, "passages": len(passages),
        "avg_words": round(statistics.mean(lens)) if lens else 0, "max_words": max(lens) if lens else 0,
        "top": [q for q in qs if hi is not None and q["points"] == hi],
        "grammar_points": [q for q in qs if q["type"] in ("어법", "서술형(어법)") and q["point"]],
    }


# ---------------- Gemini에 보내는 글 ----------------

SYSTEM_PROMPT = """당신은 한국 고등학교 영어 시험지 이미지를 글자 그대로 옮겨 적는 전문가입니다.
- 인쇄된 글자만 옮깁니다. 학생이 연필·볼펜으로 쓴 글씨, 동그라미, 체크, 밑줄 긋기, 채점 표시, 점수는 모두 무시합니다.
- 영어 지문과 선지는 철자·문장부호까지 그대로 옮깁니다. 고치거나 요약하지 않습니다.
- 인쇄된 밑줄 부분은 [u]...[/u] 로 감쌉니다. ⓐ ① (A) [A] 같은 기호와 빈칸(_____)도 그대로 둡니다.
- 2단으로 된 쪽은 왼쪽 단을 위에서 아래로 다 읽은 뒤 오른쪽 단을 읽습니다.
- 표지·유의사항·답안지 칸·OMR 안내는 문항이 아닙니다.
JSON 객체 하나로만 답합니다."""


def prompt(page_nums: list[int], total_pages: int, prev_last: str) -> str:
    first = page_nums[0] <= CHUNK
    where = ", ".join(f"이미지 {i + 1} = {p}쪽" for i, p in enumerate(page_nums))
    lines = [
        f"시험지 {total_pages}쪽 중 일부입니다 ({where}).",
        f"바로 앞 쪽의 마지막 문항 번호: {prev_last}" if prev_last else "",
        "이 쪽들에 있는 문항을 빠짐없이 옮겨 주세요. 앞 쪽에서 시작된 문항이 이어지면 같은 label 로 이어진 부분만 넣고 continues_from_previous_page 를 true 로.",
        "",
        "출력 형식:",
        "{",
        '  "header": ' + ('{"school": "학교 이름", "year": "2026", "semester": "1", "term": "1 또는 2 (1차·2차/중간·기말)", "grade": "1", "subject": "공통영어1", "date": "시험일", '
                          '"mc_count": "객관식 문항 수(적혀 있으면)", "sa_count": "서술형 문항 수(적혀 있으면)"},' if first else "null,"),
        '  "items": [',
        '    {"label": "시험지에 적힌 번호 그대로 (예: \\"7\\", \\"서술형 1\\", \\"서답형 2\\")",',
        '     "kind": "mc(객관식) 또는 sa(서술형·서답형)",',
        '     "points": "배점 숫자 (예: 3.5). 안 보이면 빈 문자열",',
        '     "page": "몇 번째 이미지에 있는지 (1부터, 문항이 시작하는 이미지)",',
        '     "stem": "발문 (예: 다음 글의 주제로 가장 적절한 것은?)",',
        '     "group": "[1~2] 처럼 지문을 같이 쓰면 \\"1-2\\", 아니면 빈 문자열",',
        '     "passage": "지문 전체. 같이 쓰는 지문은 범위의 첫 문항에만 넣고 나머지는 빈 문자열",',
        '     "box": "<보기>, 주어진 문장, 요약문 등 지문과 따로 놓인 상자 내용",',
        '     "choices": ["① 다음의 선지 글", "...5개 (기호 없이 내용만)"],',
        '     "conditions": "서술형 <조건> 내용 (객관식은 빈 문자열)",',
        '     "type": "유형 한 단어 (주제/제목/요지/주장/목적/심경/내용일치/지칭/대화/어법/어휘/빈칸/순서/삽입/무관한 문장/요약/서술형(영작)/서술형(어법)/서술형(요약)/서술형(해석))",',
        '     "point": "출제 포인트 한 줄 (예: 관계대명사 what과 that 구별, 수동태 시제 / 지문 핵심 내용을 묻는 문항이면 그 내용)",',
        '     "continues_from_previous_page": false}',
        "  ]",
        "}",
    ]
    return "\n".join(x for x in lines if x is not None)


def last_label(chunk: dict | None) -> str:
    items = (chunk or {}).get("items") or []
    labs = [norm_label(x.get("label"), "mc") for x in items if isinstance(x, dict) and x.get("label")]
    return max(labs, key=label_key) if labs else ""


def notes(qs: list[dict], st: dict) -> list[str]:
    """분석지 '출제 특징' — 숫자로 말할 수 있는 것만 코드로 씀."""
    out = []
    if st["n"]:
        out.append(f"객관식 {st['mc']}문항({_f(st['mc_points'])}점), 서술형 {st['sa']}문항({_f(st['sa_points'])}점) — 서술형 비중 {st['sa_share']}%.")
    big = [t for t in st["types"] if t["count"] >= 3 and not t["type"].startswith("서술형")]
    if big:
        out.append("많이 나온 유형: " + ", ".join(f"{t['type']} {t['count']}문항({_f(t['points'])}점)" for t in big[:4]) + ".")
    sa_types = [t for t in st["types"] if t["type"].startswith("서술형")]
    if sa_types:
        out.append("서술형 구성: " + ", ".join(f"{t['type'].replace('서술형', '').strip('()') or '기타'} {t['count']}" for t in sa_types) + ".")
    groups = {q["group"] for q in qs if q["group"]}
    if groups:
        out.append(f"한 지문에 두 문항 이상 묶은 세트 {len(groups)}개 (객관식+서술형 세트 {sum(1 for g in groups if re.search(r'서', g))}개).")
    if st["passages"]:
        out.append(f"지문 {st['passages']}개, 평균 {st['avg_words']}단어 (가장 긴 지문 {st['max_words']}단어).")
    if st["top"]:
        out.append(f"최고 배점 {_f(st['top'][0]['points'])}점: " + ", ".join(q["label"] + ("번" if q["label"].isdigit() else "") for q in st["top"][:6]) + ".")
    gp = [q for q in qs if q["type"] in ("어법", "서술형(어법)", "서술형(영작)") and q["point"]]
    if gp:
        out.append("어법·영작 포인트: " + " / ".join(re.sub(r"\s*\(.*?\)\s*$", "", q["point"])[:40] for q in gp[:8]) + ".")
    return out


def _f(x) -> str:
    if x is None:
        return "-"
    return f"{round(x, 2):.2f}".rstrip("0").rstrip(".")
