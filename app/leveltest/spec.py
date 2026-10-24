"""레벨테스트 규칙: 시험지 형식 검사, 채점, 리포트 계산.

시험지(spec) 형식 — 예전 레벨테스트와 같은 모양이라 그대로 옮겨 올 수 있음
  {"sections": [
     {"id": "listening", "title": "Listening", "instruction": "...", "audio": true,
      "groups": [
        {"part": "Part 1", "note": "...", "passage": "...", "wordbank": ["..."], "image": "그림이름",
         "questions": [
           {"n": 1, "type": "mc", "q": "...", "choices": {"A": "...", "B": "..."}, "answer": "B", "skill": "...", "image": "..."},
           {"n": 2, "type": "mc_multi", ..., "answer": ["C", "D"]},
           {"n": 3, "type": "fill", "q": "My ____ is full.", "answer": "stomach"  (또는 ["a", "b"] 여러 정답)},
           {"n": 4, "type": "order", "prompt": "...", "chunks": ["e", "w", ...], "joinChar": "", "answer": "yellow"},
           {"n": 5, "type": "short", "q": "..."}   ← 채점 안 함 (선생님이 읽어 봄)
         ]}]}]}
  문항 글의 <u>…</u> 는 밑줄.
답 키: "{section id}_{n}"
"""
import re
from collections import OrderedDict

TYPES = ("mc", "mc_multi", "fill", "order", "short")
LETTERS = "ABCDE"

BANDS = [
    {"max": 40, "label": "Low Beginner", "color": "#D0504C"},
    {"max": 60, "label": "Beginner", "color": "#E8923A"},
    {"max": 85, "label": "Intermediate", "color": "#3F9C80"},
    {"max": 100, "label": "Advanced", "color": "#3D5AA8"},
]

NARRATIVE = {
    "Listening": {
        "need": "긴 대화문과 독백을 듣고 세부 내용을 파악하는 연습이 필요합니다. 짧은 대화부터 반복해서 듣고 핵심 정보를 받아 적는 훈련을 추천합니다.",
        "mid": "전반적으로 이해하고 있으나 세부 정보 파악 연습을 더하면 좋습니다. 숫자, 시간, 장소처럼 구체적인 정보에 집중해서 듣는 연습이 도움이 될 거예요.",
        "good": "대화의 흐름과 세부 내용을 정확히 파악하고 있습니다. 좀 더 빠른 속도의 원어민 대화에도 도전해 보면 좋겠습니다.",
    },
    "Vocabulary": {
        "need": "핵심 어휘의 의미와 쓰임에 대한 반복 학습이 필요합니다. 단어를 문장 속에서 여러 번 접하며 의미를 자연스럽게 익히는 것을 추천합니다.",
        "mid": "기본 어휘는 갖추었으나 다양한 문맥에서의 활용 연습이 필요합니다. 유의어와 반의어를 함께 학습하면 어휘력을 더 탄탄히 다질 수 있어요.",
        "good": "어휘력이 우수하며 문맥 속 의미 파악도 정확합니다. 좀 더 수준 높은 어휘와 관용 표현으로 확장해도 좋겠습니다.",
    },
    "Reading": {
        "need": "글의 중심 내용을 찾고 세부 정보를 파악하는 독해 훈련이 필요합니다. 문단별로 핵심 문장을 표시하며 읽는 습관을 들이면 도움이 됩니다.",
        "mid": "전반적인 이해는 가능하나 추론이 필요한 문제에서 어려움을 보일 수 있습니다. 답의 근거를 지문에서 직접 찾아 표시하는 연습을 추천합니다.",
        "good": "다양한 유형의 지문을 정확히 이해하고 있습니다. 더 길고 복잡한 지문으로 독해 범위를 넓혀가면 좋겠습니다.",
    },
    "Writing": {
        "need": "문장 구조와 어순에 대한 기초 학습이 필요합니다. 짧은 문장을 정확한 어순으로 쓰는 연습부터 차근차근 시작하면 좋겠습니다.",
        "mid": "기본 문장 구성은 가능하나 좀 더 정확한 어순 훈련이 필요합니다. 주어-동사-목적어 순서를 의식하며 문장을 써보는 연습을 추천합니다.",
        "good": "문법과 어순을 정확히 이해하고 문장을 구성할 수 있습니다. 더 길고 복잡한 문장 쓰기에도 도전해 보면 좋겠습니다.",
    },
    "Grammar": {
        "need": "핵심 문법 개념에 대한 보충 학습이 필요합니다. 아래 오답 문항을 통해 어떤 개념이 부족한지 확인하고 기초부터 다시 정리하는 것을 추천합니다.",
        "mid": "기본 문법은 이해하고 있으나 정확도를 높이는 연습이 필요합니다. 아래 오답 문항을 다시 풀어보며 헷갈린 개념을 점검해 보세요.",
        "good": "문법 개념을 정확히 이해하고 적용할 수 있습니다. 더 복잡한 문장 구조와 고난도 어법 문제로 실력을 확장해도 좋겠습니다.",
    },
    "Alphabet": {
        "need": "알파벳과 소리의 연결에 대한 반복 연습이 필요합니다. 매일 조금씩 알파벳 소리를 듣고 따라 말하는 연습을 추천합니다.",
        "mid": "기본적인 알파벳 인지는 되어 있으나 좀 더 정확한 연습이 필요합니다. 헷갈리는 글자를 따로 모아 반복 학습하면 좋겠습니다.",
        "good": "알파벳과 소리를 정확하게 연결하고 있습니다. 단어 단위의 파닉스 학습으로 자연스럽게 넘어가면 좋겠습니다.",
    },
}

GENERIC = {
    "need": "이 영역은 기초부터 다시 다지는 학습이 필요합니다. 틀린 문항을 중심으로 개념을 차근차근 정리해 보세요.",
    "mid": "기본은 갖추었으나 정확도를 높이는 연습이 필요합니다. 헷갈린 문항을 다시 풀어보며 점검해 보세요.",
    "good": "이 영역은 잘 이해하고 있습니다. 한 단계 높은 문제로 실력을 넓혀 가면 좋겠습니다.",
}


def band_for(score: int) -> dict:
    for b in BANDS:
        if score <= b["max"]:
            return b
    return BANDS[-1]


def narrative(cat: str, pct: int) -> str:
    tier = "need" if pct < 40 else ("mid" if pct < 70 else "good")
    return (NARRATIVE.get(cat) or GENERIC)[tier]


def grammar_skill(q: dict) -> str:
    if q.get("skill"):
        return q["skill"]
    t = q.get("q") or ""
    for pat, name in ((r"to\s*부정사", "to부정사"), ("분사구문", "분사구문"), ("관계대명사", "관계대명사"), ("관계부사", "관계부사"), ("수동태", "수동태"),
                      ("가정법", "가정법"), ("동명사", "동명사"), ("비교급|최상급|원급", "비교급·최상급"), ("형식", "문장의 형식")):
        if re.search(pat, t):
            return name
    return "기타 문법 개념"


def reading_skill(t: str) -> str:
    t = t or ""
    for pat, name in ((r"들어가기에 가장 적절한 곳|주어진 문장이 들어가기", "문장 삽입"), (r"순서로 가장 적절한|글의 순서", "글의 순서"),
                      (r"제목으로 가장 적절한|제목으로 알맞은", "제목 추론"), (r"빈칸에 (들어갈|알맞은)", "빈칸 추론"), (r"(?i)가리키는|지칭|refers to", "지칭 추론"),
                      ("분위기", "분위기 파악"), ("심경|심정", "심경 파악"), ("요지|주장|요약", "요지·주장 파악"),
                      (r"(?i)일치하지 않는|NOT true|다른 것은", "세부내용 파악(불일치 찾기)")):
        if re.search(pat, t):
            return name
    return "세부내용 파악"


# ---------------------------------------------------------------- 형식 검사

def check(spec) -> tuple[dict, list[str]]:
    """붙여 넣은 시험지를 정리하고 문제점 목록을 돌려줌. 문제가 있으면 저장하지 않음."""
    errs: list[str] = []
    if not isinstance(spec, dict) or not isinstance(spec.get("sections"), list) or not spec["sections"]:
        return {}, ["'sections' 목록이 없어요."]
    seen_ids = set()
    out = {"sections": []}
    for si, sec in enumerate(spec["sections"]):
        if not isinstance(sec, dict):
            errs.append(f"{si + 1}번째 영역 형식이 이상해요.")
            continue
        sid = re.sub(r"[^a-z0-9_]", "", str(sec.get("id") or sec.get("title") or f"s{si + 1}").lower()) or f"s{si + 1}"
        if sid in seen_ids:
            errs.append(f"영역 id '{sid}'가 두 번 나와요.")
        seen_ids.add(sid)
        title = str(sec.get("title") or sid).strip()
        audio = bool(sec.get("audio")) or sid == "listening"
        groups, nums = [], set()
        for gi, g in enumerate(sec.get("groups") or []):
            qs = []
            for q in g.get("questions") or []:
                t = q.get("type") or "mc"
                n = q.get("n")
                where = f"{title} {n}번"
                if t not in TYPES:
                    errs.append(f"{where}: 문항 종류 '{t}'를 모르겠어요 ({', '.join(TYPES)} 중 하나).")
                    continue
                if not isinstance(n, int):
                    errs.append(f"{title}: 번호(n)가 숫자가 아닌 문항이 있어요.")
                    continue
                if n in nums:
                    errs.append(f"{where}: 번호가 겹쳐요.")
                nums.add(n)
                row = {k: q[k] for k in ("n", "q", "skill", "image", "prompt", "joinChar") if k in q}
                row["type"] = t
                if t in ("mc", "mc_multi"):
                    ci = q.get("choiceImages") if isinstance(q.get("choiceImages"), dict) else {}
                    if ci:
                        row["choiceImages"] = {str(k): str(v) for k, v in ci.items()}
                    ch = q.get("choices") or {k: "" for k in ci}
                    if not isinstance(ch, dict) or len(ch) < 2:
                        errs.append(f"{where}: 보기(choices)가 2개 이상 있어야 해요.")
                        continue
                    row["choices"] = {str(k): str(v) for k, v in ch.items()}
                    ans = q.get("answer")
                    ans_list = ans if isinstance(ans, list) else [ans]
                    if not ans_list or any(str(a) not in row["choices"] for a in ans_list):
                        errs.append(f"{where}: 정답 '{ans}'가 보기에 없어요.")
                        continue
                    row["answer"] = [str(a) for a in ans_list] if t == "mc_multi" else str(ans_list[0])
                elif t in ("fill", "order"):
                    ans = q.get("answer")
                    if not ans:
                        errs.append(f"{where}: 정답(answer)이 없어요.")
                        continue
                    row["answer"] = ans if isinstance(ans, list) else str(ans)
                    if t == "order":
                        if not isinstance(q.get("chunks"), list) or len(q["chunks"]) < 2:
                            errs.append(f"{where}: 배열할 조각(chunks)이 없어요.")
                            continue
                        row["chunks"] = [str(c) for c in q["chunks"]]
                        row.setdefault("joinChar", " ")
                qs.append(row)
            g2 = {k: g[k] for k in ("part", "note", "passage", "image") if g.get(k)}
            if isinstance(g.get("wordbank"), list):
                g2["wordbank"] = [str(w) for w in g["wordbank"]]
            g2["questions"] = qs
            groups.append(g2)
        if not any(g["questions"] for g in groups):
            errs.append(f"'{title}' 영역에 문항이 없어요.")
        out["sections"].append({"id": sid, "title": title, "instruction": str(sec.get("instruction") or ""), "audio": audio, "groups": groups})
    if isinstance(spec.get("timeLimitSec"), int):
        out["minutes"] = spec["timeLimitSec"] // 60
    return out, errs


def image_keys(spec: dict) -> set[str]:
    """시험지에서 쓰는 그림 이름 전부 (그림 올리기 확인용)."""
    keys = set()
    for sec in spec.get("sections") or []:
        for g in sec.get("groups") or []:
            if g.get("image"):
                keys.add(g["image"])
            for q in g.get("questions") or []:
                if q.get("image"):
                    keys.add(q["image"])
                keys.update((q.get("choiceImages") or {}).values())
    return keys


def items(spec: dict) -> list[dict]:
    """[{key, section, sid, q}] 시험 순서대로."""
    out = []
    for sec in spec.get("sections") or []:
        for g in sec.get("groups") or []:
            for q in g.get("questions") or []:
                out.append({"key": f"{sec['id']}_{q['n']}", "section": sec["title"], "sid": sec["id"], "q": q})
    return out


def public(spec: dict) -> dict:
    """학생 화면용: 정답 빼고."""
    secs = []
    for sec in spec.get("sections") or []:
        groups = []
        for g in sec.get("groups") or []:
            groups.append({**{k: v for k, v in g.items() if k != "questions"},
                           "questions": [{k: v for k, v in q.items() if k != "answer"} for q in g.get("questions") or []]})
        secs.append({**{k: v for k, v in sec.items() if k != "groups"}, "groups": groups})
    return {"sections": secs}


def clean_answers(spec: dict, raw: dict) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for it in items(spec):
        v = raw.get(it["key"])
        if v is None:
            continue
        v = str(v).strip()[:2000]
        if v:
            out[it["key"]] = v
    return out


# ---------------------------------------------------------------- 채점

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower()).replace("’", "'").rstrip(".")


def is_correct(q: dict, given: str | None) -> bool:
    if not given:
        return False
    t, ans = q["type"], q.get("answer")
    if t in ("fill", "order"):
        return any(_norm(given) == _norm(a) for a in (ans if isinstance(ans, list) else [ans]))
    if t == "mc_multi":
        return sorted(x for x in given.split(",") if x) == sorted(ans)
    return given == ans


def _text(q: dict, v) -> str:
    if v is None or v == "" or v == []:
        return "(무응답)"
    if q.get("choices") is not None:
        letters = v if isinstance(v, list) else str(v).split(",")
        return " / ".join(f"{l}) {q['choices'].get(l, '')}".rstrip() for l in letters if l)
    return str(v if not isinstance(v, list) else v[0])


def report(spec: dict, answers: dict) -> dict:
    rows, cats = [], OrderedDict()
    for i, it in enumerate(items(spec), 1):
        q, given = it["q"], answers.get(it["key"])
        graded = q["type"] != "short"
        ok = graded and is_correct(q, given)
        rows.append({"no": i, "n": q["n"], "section": it["section"], "graded": graded, "ok": ok, "q": q.get("q") or q.get("prompt") or "",
                     "answer": _text(q, q.get("answer")) if graded else "", "given": _text(q, given)[:500], "skill": q.get("skill", "")})
        if graded:
            c = cats.setdefault(it["section"], {"correct": 0, "total": 0, "wrong": [], "right": []})
            c["total"] += 1
            c["correct"] += ok
            (c["right"] if ok else c["wrong"]).append(q)
    total = sum(c["total"] for c in cats.values())
    correct = sum(c["correct"] for c in cats.values())
    score = round(100 * correct / total) if total else 0
    band = band_for(score)
    cat_rows = []
    for name, c in cats.items():
        pct = round(100 * c["correct"] / c["total"]) if c["total"] else 0
        detail = None
        if name == "Grammar":
            skills = OrderedDict()
            for q in c["wrong"]:
                skills.setdefault(grammar_skill(q), []).append(q["n"])
            detail = {"type": "grammar", "wrong": [{"skill": k, "nums": sorted(v)} for k, v in skills.items()]}
        elif name == "Reading":
            wrong = list(OrderedDict.fromkeys(reading_skill(q.get("q", "")) for q in c["wrong"]))
            right = [s for s in OrderedDict.fromkeys(reading_skill(q.get("q", "")) for q in c["right"]) if s not in wrong]
            detail = {"type": "reading", "wrong": wrong, "right": right}
        cat_rows.append({"name": name, "correct": c["correct"], "total": c["total"], "pct": pct, "band": band_for(pct)["label"],
                         "narrative": narrative(name, pct), "detail": detail})
    return {"score": score, "correct": correct, "total": total, "band": band, "cats": cat_rows, "items": rows,
            "short": [r for r in rows if not r["graded"]]}
