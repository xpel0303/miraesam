"""개발용 가짜 Gemini (환경변수 FAKE_LLM=1 일 때만 사용).

실제 키 없이 화면·작업 대기열·PDF 흐름을 시험하기 위한 것입니다. 문제 품질과는 무관합니다.
지문에 FAKE_FAIL 이 있으면 늘 규칙 위반 답을, FAKE_REPAIR 가 있으면 첫 답만 위반하고 두 번째는 정상 답을 줍니다.
"""
import re
import time


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if len(p.split()) >= 3]


def _comprehension(prompt: str) -> dict:
    passage = prompt.split("지문:\n", 1)[-1]
    sents = _sentences(passage) or ["This is a short test sentence."]
    pick = lambda i: sents[i % len(sents)]
    pattern = ["O", "X", "O", "X", "O"]

    def ox(lang: str, deep: bool):
        items = []
        for i in range(5):
            s = pick(i + (2 if deep else 0))
            a = pattern[(i + (1 if deep else 0)) % 5]
            if lang == "ko":
                q = f"[가짜] 지문 {i + 1}번째 내용과 {'일치하는' if a == 'O' else '어긋나는'} 문장입니다." + (" 심화 문항이라 조금 더 길게 씁니다." if deep else "")
            else:
                q = s if a == "O" else "It is not true that " + s[0].lower() + s[1:]
            items.append({"q": q, "a": a, "why": s[:60]})
        return items

    compose_src = [s for s in sents if 4 <= len(s.split()) <= 16] or sents
    compose = [
        {"ko": f"[가짜] {i + 1}번 문장의 우리말", "answer": compose_src[i % len(compose_src)], "point": "가짜 어법 설명", "source": "지문" if i < len(compose_src) else "보충"}
        for i in range(5)
    ]
    words = sorted({w.lower() for w in re.findall(r"[A-Za-z]{6,}", passage)}, key=len, reverse=True)[:5]
    while len(words) < 5:
        words.append(f"word{len(words) + 1}")
    out = {
        "ox_ko_short": ox("ko", False),
        "ox_en_short": ox("en", False),
        "ox_ko_deep": ox("ko", True),
        "ox_en_deep": ox("en", True),
        "compose": compose,
        "vocab": [{"word": w, "meaning": "[가짜] 뜻"} for w in words],
    }
    if '"title"' in prompt.split("추가로 만들 것:", 1)[-1].split("\n", 1)[0]:
        out["title"] = "가짜 제목 " + " ".join(passage.split()[:3])
    if '"passage_ko"' in prompt.split("추가로 만들 것:", 1)[-1].split("\n", 1)[0]:
        out["passage_ko"] = "[테스트용 가짜 해석] " + " ".join(f"({i + 1}번 문장 해석)" for i in range(len(sents)))
    if "FAKE_FAIL" in passage or ("FAKE_REPAIR" in passage and "직전 답에" not in prompt):
        out["vocab"] = out["vocab"][:3]
    return out


def _analysis(prompt: str) -> dict:
    sents = re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M)
    if "neuroethics" in prompt:  # 선생님이 보내 준 양식 지문 → 실제 분석본 내용 (교과서 지문이라 저장소에는 없음)
        try:
            from .devfake_analysis import build
            return build([(int(n), s) for n, s in sents], "정리 그림(visual)" in prompt)
        except ImportError:
            pass
    rows = []
    for n, s in sents:
        n = int(n)
        tags = []
        for w, typ, label in (("However", "connective", "역접"), ("which", "grammar", "관계대명사(주격)"), ("that", "grammar", "접속사 that"), ("what", "grammar", "관계대명사 what")):
            if re.search(rf"\b{w}\b", s):
                tags.append({"text": w, "type": typ, "label": label})
        long = [w for w in re.findall(r"[A-Za-z]{7,}", s)][:1]
        if long:
            tags.append({"text": long[0], "type": "vocab", "label": "[가짜] 뜻"})
        rows.append({
            "n": n, "tags": tags, "chips": ["[가짜] 구문"] + (["관계사"] if any(t["type"] == "grammar" for t in tags) else []),
            "ko": f"[가짜 해석] {n}번 문장의 우리말 해석입니다.",
            "points": [{"type": ["grammar", "reading", "topic"][n % 3], "text": "[가짜 포인트] 이 문장은 시험에 잘 나와. 핵심 구조를 확인해 두자."}],
            "insertion": s.startswith("However"),
        })
    if "FAKE_FAIL" in prompt:
        rows = rows[:-1]
    words = sorted({w.lower() for w in re.findall(r"[A-Za-z]{7,}", prompt)}, key=len, reverse=True)[:8]
    return {
        "title_en": "Fake Title For Testing", "title_ko": "테스트용 가짜 제목",
        "sentences": rows,
        "summary": {"topic": "[가짜] 글의 요지 한 문장입니다.",
                    "flow": [{"ko": "도입", "en": "Intro"}, {"ko": "문제 제기", "en": "Problem"}, {"ko": "해결", "en": "Solution"}, {"ko": "결론", "en": "Conclusion"}],
                    "background": "[가짜 배경지식] 이 지문과 관련된 이론을 설명하는 단락입니다. 실제 Gemini가 쓰면 4~6문장으로 채워집니다."},
        "vocab": [{"word": w, "meaning": "[가짜] 뜻", "syn": "synonym", "ant": ""} for w in words],
    }


def _vocab(prompt: str) -> dict:
    given = re.findall(r"^- ([^\n(]+?)(?: \(|$)", prompt, re.M)
    if given:
        words = [g.strip() for g in given]
    else:
        n = int((re.search(r"정확히 (\d+)개", prompt) or [0, 10])[1])
        passage = prompt.split("지문:\n", 1)[-1]
        words = sorted({w.lower() for w in re.findall(r"[A-Za-z]{6,}", passage)}, key=len, reverse=True)[:n]
    return {"words": [{"word": w, "pos": "v.", "meaning": f"[가짜] {w}의 뜻, 두번째 뜻", "syn": "similar", "ant": "",
                       "example": f"This sentence uses {w}.", "example_ko": "[가짜] 예문 해석"} for w in words]}


def fake_generate(prompt: str, *, system: str | None = None, json_mode: bool = False):
    time.sleep(1.2)
    if system and "영어 단어장" in system:
        return _vocab(prompt)
    if system and "어법 문제를 출제" in system:
        return _grammar(prompt)
    if system and "독해 문제를 출제" in system:
        return _variant(prompt)
    if system and "문장 시험지" in system:
        return _sentence(prompt)
    if system and "원문을 변형" in system:
        return _paraphrase(prompt)
    if system and "내용이해 워크북" in system:
        return _comprehension(prompt)
    if system and "지문 분석본" in system:
        return _analysis(prompt)
    if system and "4컷 정리 그림" in system:
        return {"visual": {"title": "[가짜] 한눈에 정리", "title_mark": "정리", "layout": "contrast", "message": "[가짜] 한 줄 결론",
                           "panels": [{"icon": i, "head": f"[가짜] {k + 1}칸", "text": "[가짜] 설명", "mark": "설명", "scene": "A student reading a book."}
                                      for k, i in enumerate(("book", "idea"))]}}
    if system and "시험지 이미지를 글자 그대로" in system:
        return _exam(prompt)
    if json_mode:
        return {"reply": "[가짜 응답] 선생님, 오늘도 좋은 수업 되세요."}
    return "[가짜 응답]"


def _grammar(prompt: str) -> dict:
    if "원래 지문:" in prompt:
        passage = prompt.split("원래 지문:\n", 1)[1].split("\n\n출력 형식", 1)[0].strip()
        words = re.findall(r"\b[a-z]{4,}\b", passage)
        picks = []
        for w in words:
            if w not in picks:
                picks.append(w)
            if len(picks) == 5:
                break
        count_mode = "어법 개수" in prompt
        wrong_idx = {1, 3} if count_mode else {2}
        if "FAKE_FAIL" in passage:
            wrong_idx = set()
        marks = [{"text": w, "wrong": i in wrong_idx, "fix": w + "(고침)" if i in wrong_idx else "", "why": "[가짜] 이유"} for i, w in enumerate(picks)]
        return {"passage": passage, "marks": marks, "explain": "[가짜] 해설"}
    counts = dict(re.findall(r"- (\w+) \([^)]*\): (\d+)개", prompt))
    n = lambda k: int(counts.get(k, 0))
    return {
        "paren": [{"sentence": f"I enjoy {{to read/reading}} book number {i + 1}.", "answer": "reading", "explain": "[가짜] enjoy는 동명사를 목적어로"} for i in range(n("paren"))],
        "blank": [{"sentence": f"She made her son ____ room {i + 1}.", "choices": ["clean", "to clean", "cleaning", "cleaned", "cleans"], "answer": 1, "explain": "[가짜] 사역동사 make + 원형부정사"} for i in range(n("blank"))],
        "pick": [{"ask": "wrong", "choices": ["He has lived here since 2010.", "She suggested going out.", "They was happy.", "I am used to waking up early.", "It is worth visiting."], "answer": 3, "explain": "[가짜] They were"} for _ in range(n("pick"))],
        "arrange": [{"ko": f"[가짜] 나는 {i + 1}번째 책 읽는 것을 즐긴다.", "answer": f"I enjoy reading the book number {i + 1}.", "condition": "", "explain": "[가짜] 해설"} for i in range(n("arrange"))],
        "transform": [{"original": "Because he was tired, he went to bed early.", "instruction": "분사구문으로 바꾸시오", "answer": "Being tired, he went to bed early.", "explain": "[가짜] 해설"} for _ in range(n("transform"))],
    }


def _variant(prompt: str) -> dict:
    passage = prompt.split("\n지문:\n", 1)[-1].strip()
    asked = re.findall(r'^  "(\w+)":', prompt, re.M)
    words = re.findall(r"\b[a-z]{5,}\b", passage)
    phrase = " ".join(passage.split()[3:7]).rstrip(".,")
    out = {}
    for t in asked:
        if t in ("topic", "title", "gist"):
            n = 3 if "FAKE_FAIL" in passage and t == "topic" else 5
            out[t] = {"choices": [f"[가짜] {t} 보기 {i + 1}" for i in range(n)], "answer": 2, "explain": "[가짜] 해설"}
        elif t == "blank":
            out[t] = {"phrase": phrase, "choices": [phrase, "fake one", "fake two", "fake three", "fake four"], "answer": 1, "explain": "[가짜] 해설"}
        elif t == "irrelevant":
            out[t] = {"sentence": "[Fake] Some people prefer to study while listening to music.", "explain": "[가짜] 해설"}
        elif t == "summary":
            out[t] = {"summary": "Learning is (A) when students (B) ideas.", "choices": [["deeper", "explain"], ["faster", "copy"], ["harder", "ignore"], ["easier", "memorize"], ["slower", "repeat"]], "answer": 1, "explain": "[가짜] 해설"}
        elif t == "match":
            out[t] = {"ask": "wrong", "choices": [f"[가짜] 진술 {i + 1}" for i in range(5)], "answer": 4, "explain": "[가짜] 해설"}
        elif t == "vocab":
            picks = []
            for w in words:
                if w not in picks:
                    picks.append(w)
                if len(picks) == 5:
                    break
            out[t] = {"passage": passage, "marks": [{"text": w, "wrong": i == 2, "fix": w + "(원래)" if i == 2 else "", "why": "[가짜]"} for i, w in enumerate(picks)], "explain": "[가짜] 해설"}
    return out


def _sentence(prompt: str) -> dict:
    rows = []
    for n, s in re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M):
        keys = [w for w in re.findall(r"[A-Za-z]{6,}", s)][:1]
        rows.append({"n": int(n), "ko": f"[가짜 해석] {n}번 문장", "keys": keys})
    if "FAKE_FAIL" in prompt:
        rows = rows[:-1]
        for r in rows:
            r["ko"] = ""
    return {"sentences": rows}


def _paraphrase(prompt: str) -> dict:
    original = prompt.split("\n원문:\n", 1)[-1].strip()
    if "FAKE_FAIL" in original:
        return {"text": original, "notes": ["[가짜] 바꾸지 않음"]}
    step = 6 if "강도: 약" in prompt else 3 if "강도: 중" in prompt else 2
    words = original.split()
    out = [("alt" + w.lower() if i % step == 1 and w[:1].isalpha() else w) for i, w in enumerate(words)]
    return {"text": " ".join(out), "notes": ["[가짜] 어휘 교체", "[가짜] 구조 변경"]}


def _exam(prompt: str) -> dict:
    """기출 분석: 전라고 2026 1학기 2차 시험지를 사람이 직접 옮긴 실제 데이터를 쪽 번호에 맞춰 돌려줌.
    (어떤 시험지를 올려도 이 내용이 나옴 — 화면·합치기·통계 시험용)"""
    import json
    from pathlib import Path

    if "FAKE_FAIL" in prompt:
        return {"oops": True}
    m = re.search(r"이미지 1 = (\d+)쪽", prompt)
    first = int(m.group(1)) if m else 1
    fx = Path(__file__).parent / "exam" / "fixtures" / "jeonra_2026_1_2.json"
    if not fx.exists():  # 시험지 원문은 저작권 때문에 공개 저장소에 올리지 않음 → 짧은 가짜 문항
        return {"header": {"school": "가짜고등학교", "year": "2026", "semester": "1", "term": "1", "grade": "1"} if first <= 2 else None,
                "items": [{"label": str(first * 10 + k), "kind": "mc", "points": "4", "page": 1, "stem": "다음 글의 주제로 가장 적절한 것은?",
                           "passage": "This is a fake passage for development. " * 6, "choices": ["a", "b", "c", "d", "e"], "type": "주제"} for k in range(3)]}
    data = json.loads(fx.read_text())
    key = str(((first - 1) // 2) * 2)
    c = data.get(key) or {"header": None, "items": []}
    pages = [int(x) for x in re.findall(r"= (\d+)쪽", prompt)]
    if pages and len(pages) == 1 and pages[0] % 2 == 0:  # 앞 쪽이 빈 쪽이라 한 쪽만 온 경우
        c = {"header": c.get("header"), "items": [dict(i, page=1) for i in c["items"] if i.get("page") == 2]}
    return c


def fake_image(prompt: str, aspect: str = "16:9") -> bytes:
    """가짜 정리 그림 (PNG). 진짜 그림 모델 대신 제목·칸 모양만 흉내 냄."""
    import io
    import glob

    from PIL import Image, ImageDraw, ImageFont

    w, h = (1600, 900) if aspect == "16:9" else (1200, 1200)
    img = Image.new("RGB", (w, h), "#fdf8ee")
    d = ImageDraw.Draw(img)
    fonts = glob.glob("/usr/share/fonts/**/NotoSansCJK-Bold.ttc", recursive=True)
    font = lambda size: ImageFont.truetype(fonts[0], size) if fonts else ImageFont.load_default()
    title = (re.findall(r'headline[^"]*"([^"]+)"', prompt) or ["[가짜] 정리 그림"])[0]
    d.text((60, 40), title[:40], fill="#1b2a4a", font=font(64))
    cols = ("#5b7bb2", "#e9a23b", "#5aa469")
    n = max(2, min(3, len(re.findall(r"^Panel \d", prompt, re.M)) or 2))
    pw = (w - 120 - 40 * (n - 1)) // n
    for i in range(n):
        x = 60 + i * (pw + 40)
        d.rounded_rectangle((x, 160, x + pw, h - 170), 28, fill=cols[i % 3])
        d.rectangle((x + 30, h - 290, x + pw - 30, h - 210), fill="#ffe14d")
        d.text((x + 50, h - 280), f"[가짜] 칸 {i + 1}", fill="#222", font=font(44))
    d.rectangle((0, h - 120, w, h), fill="#1b2a4a")
    d.text((60, h - 95), "[가짜 그림] 실제로는 Gemini 그림 모델이 그립니다", fill="white", font=font(44))
    time.sleep(0.2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
