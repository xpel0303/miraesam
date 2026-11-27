"""개발용 가짜 Gemini (환경변수 FAKE_LLM=1 일 때만 사용).

실제 키 없이 화면·작업 대기열·PDF 흐름을 시험하기 위한 것입니다. 문제 품질과는 무관합니다.
지문에 FAKE_FAIL 이 있으면 늘 규칙 위반 답을, FAKE_REPAIR 가 있으면 첫 답만 위반하고 두 번째는 정상 답을 줍니다.
"""
import json
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
        {"ko": f"[가짜] {i + 1}번 문장의 우리말", "answer": compose_src[i % len(compose_src)], "point": "가짜 어법 설명", "grammar": "", "target": "", "source": "지문" if i < len(compose_src) else "보충"}
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
    if system and "교과 연계 분석가" in system:
        t = prompt.split("[영어 지문]")[-1].lower()
        ids = ["u59", "u55"] if any(w in t for w in ("energy", "climate", "carbon", "ecosystem")) else ["u44", "u48"]
        return json.dumps({"topic": "가짜 주제", "links": [{"id": i, "fit": "높음" if k == 0 else "보통", "reason": "지문의 내용이 이 단원 개념과 이어져요 (가짜 답).",
                                                         "terms": ["지속가능성"]} for k, i in enumerate(ids)]}, ensure_ascii=False)
    if system and "직전대비" in system:
        return _final(prompt)
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
    if system and "본문 워크북" in system:
        return _textbook(prompt)
    if system and "문법 문제 출제 전문가" in system:
        import random
        k = random.randint(0, 10**6)
        return {"questions": [{"kind": "mc", "qtype": "객관식", "level": "중", "instr": "다음 빈칸에 알맞은 것은?", "stem": f"[가짜 {k}-{i}] I ____ a cat.",
                               "choices": "① has ② have ③ having ④ to have ⑤ had been", "answer": "②", "expl": "[가짜] 해설"} for i in range(8)]
                + [{"kind": "short", "qtype": "빈칸 쓰기", "level": "하", "instr": "빈칸에 알맞은 말을 쓰시오.", "stem": f"[가짜 {k}-s{i}] She ____ (go) home.", "choices": "", "answer": "goes", "expl": "[가짜]"} for i in range(2)]}
    if system and "영어 지문을 글자 그대로 옮겨" in system:
        return "[19]\n[가짜] Dolphins are known for their intelligence. They communicate using clicks and whistles.\n=====\n[18]\n[가짜] Second passage text here, which is long enough to be saved."
    if system and "배열 영작 문항이 주어진 목표어법" in system:
        return {"results": [{"no": i, "ok": True, "why": "가짜"} for i in range(1, 6)]}
    if system and "문법 문제 검토자" in system:
        return {"results": [{"i": i, "mine": "x", "agree": i != 3} for i in range(10)]}
    if system and "개념 설명 카드" in system:
        return {"title": "[가짜] 개념", "points": ["[가짜] 규칙 하나", "[가짜] 규칙 둘", "[가짜] 주의할 점"], "forms": "have/has + p.p.",
                "examples": [{"en": "I have lived here for ten years.", "ko": "[가짜] 해석", "hl": "have lived"}, {"en": "She has finished it.", "ko": "[가짜] 해석", "hl": "has finished"}],
                "tip": "[가짜] 시험 함정"}
    if system and "흐름분석" in system:
        words = [w for w in re.findall(r"[A-Za-z]{6,}", prompt.split("지문:")[-1])][:8] or ["learning"] * 8
        return {"title": "[가짜] 흐름 제목", "gist": "[가짜] 핵심 내용 한두 줄, 개조식 정리", "top": "[가짜] 중심 화제",
                "branches": [{"head": "[가짜] 갈래 1", "body": "[가짜] 첫째 갈래 내용 설명"}, {"head": "[가짜] 갈래 2", "body": "[가짜] 둘째 갈래 내용 설명"}],
                "result": {"head": "[가짜] 결론", "items": ["[가짜] 항목 하나", "[가짜] 항목 둘", "[가짜] 항목 셋"]},
                "comic": [{"words": [words[2 * i % len(words)]], "ko": f"[가짜] {i + 1}칸 내용", "scene": "A student thinks about the idea."} for i in range(4)]}
    if system and "지문 정리본" in system:
        return _summary(prompt)
    if system and "지문 분석본" in system:
        return _analysis(prompt)
    if system and "4컷 정리 그림" in system:
        return {"visual": {"title": "[가짜] 한눈에 정리", "title_mark": "정리", "layout": "contrast", "message": "[가짜] 한 줄 결론",
                           "panels": [{"icon": i, "head": f"[가짜] {k + 1}칸", "text": "[가짜] 설명", "mark": "설명", "scene": "A student reading a book."}
                                      for k, i in enumerate(("book", "idea"))]}}
    if system and "특정 학교의 지난 시험 문항" in system:
        return _mock(prompt)
    if system and "입시 컨설턴트" in system:
        return {"summary": ["[가짜] 선택형 20 + 서답형 8 구성이 반복됨", "[가짜] 어법 '모두 고르시오' 유형이 매회 나옴"],
                "vocab": "[가짜] 영영 뜻풀이와 공통 어휘 단답형", "grammar": ["[가짜] 분사구문", "[가짜] 가정법"],
                "reading": "[가짜] 교과서 본문 세트 문항", "writing": "[가짜] 앞 구절 2점 + 뒤 구절 3점 조건 영작",
                "strategy": ["[가짜] 본문 문장 변형 연습"]}
    if system and "시험지 이미지를 글자 그대로" in system:
        return _exam(prompt)
    if system and "설명회 슬라이드" in system:
        keys = re.findall(r"^- ([a-z]+(?::[a-z0-9]+)?): ", prompt, re.M)
        ch = [k for k in keys if k.startswith(("types", "mcsa", "trend:sa", "trend:words"))]
        st = [k for k in keys if k.startswith("stats")]
        tb = [k for k in keys if k.startswith(("grammar", "trend:types", "books"))]
        n = lambda t: f"[가짜] {t} 발표 메모입니다. 천천히 설명해 주세요."  # noqa: E731
        sl = [{"layout": "cover", "title": "[가짜] 2026 겨울 설명회", "sub": "우리 아이 영어, 이렇게 준비해요", "foot": "12월 7일(토) 오후 2시", "notes": n("인사")},
              {"layout": "section", "title": "1. 시험 들여다보기", "sub": "숫자로 보는 출제 경향", "notes": n("장")}]
        sl += [{"layout": "stats", "title": "[가짜] 한눈에 보기", "data": k, "notes": n("숫자")} for k in st[:1]]
        sl += [{"layout": "chart", "title": "[가짜] 그래프", "data": k, "bullets": ["[가짜] 서술형이 점점 늘어요", "[가짜] 어법 배점이 커요"], "notes": n("그래프")} for k in ch[:3]]
        sl += [{"layout": "table", "title": "[가짜] 표", "data": k, "notes": n("표")} for k in tb[:1]]
        sl += [{"layout": "section", "title": "2. 우리 학원은", "sub": "이렇게 준비합니다", "notes": n("장")},
               {"layout": "bullets", "title": "[가짜] 수업 방식", "bullets": ["매일 수업 기록을 학부모 앱으로", "학교별 기출 분석으로 맞춤 대비", "단어 숙제 앱으로 매일 복습", "월간 리포트로 한 달 정리"], "notes": n("방식")},
               {"layout": "two", "title": "[가짜] 비교", "left": {"head": "지금까지", "bullets": ["감으로 공부", "결과만 확인"]}, "right": {"head": "앞으로", "bullets": ["데이터로 공부", "과정을 매일 확인"]}, "notes": n("비교")},
               {"layout": "timeline", "title": "[가짜] 겨울 일정", "steps": [{"when": "12월 1주", "what": "진단 테스트"}, {"when": "12월 3주", "what": "문법 집중"}, {"when": "1월", "what": "독해·서술형"}, {"when": "2월", "what": "새 학기 예습"}], "notes": n("일정")},
               {"layout": "closing", "title": "[가짜] 함께 준비해요", "sub": "상담은 언제든지 환영합니다", "foot": "상담 010-0000-0000", "notes": n("마무리")}]
        return {"title": "[가짜] 2026 겨울 설명회", "slides": sl}
    if system and "교재 목차 사진" in system:
        return {"title": "[가짜] 수능특강 영어", "units": [f"{i}강 [가짜] 단원 {i}" for i in range(1, 13)] + ["Mini Test 1"]}
    if system and "월간 학습 리포트" in system:
        return {"grade": "좋음", "summary": "[가짜] 숙제를 빠짐없이 해 오며 단어 점수가 꾸준히 올랐어요.",
                "strength": "[가짜] 이번 달 숙제는 한 번도 빠지지 않았습니다. 단어 시험은 평균 88점으로 반 평균보다 6점 높았어요. 특히 Day 5부터는 거의 틀리지 않았습니다. 수업 중 질문도 부쩍 늘었어요. 모르는 걸 그냥 넘기지 않는 태도가 좋아 보입니다.",
                "goal": "[가짜] 다음 달에는 Grammar note 3단원까지 나갈 예정입니다. 관계대명사 부분에서 실수가 조금 있었어요. 수업 시작 5분 동안 짧은 문장 영작으로 다시 잡아 보려고 합니다. 단어는 지금처럼 하루 30개씩 유지하면 충분해요. 서술형 문장 쓰기 연습을 조금씩 늘려 가겠습니다.",
                "message": "[가짜] 한 달 동안 정말 성실하게 따라와 주었습니다. 처음엔 긴 지문을 어려워했는데, 요즘은 끝까지 읽어 내려고 해요. 집에서 단어 시험지를 한 번만 같이 봐 주시면 큰 힘이 됩니다. 궁금하신 점은 언제든 편하게 연락 주세요. 다음 달에도 잘 이끌어 보겠습니다."}
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
        elif t in ("grammar", "sa_grammar", "definition"):
            picks = []
            for w in words:
                if w not in picks:
                    picks.append(w)
                if len(picks) == 5:
                    break
            bad = {1, 3} if t == "sa_grammar" else {2}
            out[t] = {"passage": passage, "marks": [{"text": w, "wrong": i in bad, "fix": w + "(고침)" if i in bad else "", "why": "[가짜]",
                                                     "definition": f"[fake] meaning of {w}"} for i, w in enumerate(picks)], "explain": "[가짜] 해설"}
        elif t in ("purpose", "mood", "right_sent", "match_en_r", "match_ko_r", "match_en_w", "match_ko_w"):
            out[t] = {"choices": [f"[가짜] {t} 보기 {i + 1}" for i in range(5)], "answer": 3, "explain": "[가짜] 해설"}
        elif t in ("blank_word", "implied"):
            w = phrase.split()[0] if t == "blank_word" else phrase
            out[t] = {"word" if t == "blank_word" else "phrase": w, "choices": [w, "fake", "wrong", "other", "none"], "answer": 1, "explain": "[가짜] 해설"}
        elif t == "blank_sent":
            sents = re.split(r"(?<=[.!?])\s+", passage)
            sent = next((x for x in sents[1:] if len(x.split()) >= 7), sents[-1]).rstrip(".")
            out[t] = {"sentence": sent, "choices": [f"[가짜] 문장 보기 {i + 1}" for i in range(5)], "answer": 2, "explain": "[가짜] 해설"}
        elif t == "def_pick":
            picks = list(dict.fromkeys(words))[:5]
            out[t] = {"definition": f"[fake] meaning of {picks[1]}", "choices": picks, "answer": 2, "explain": "[가짜] 해설"}
        elif t in ("refer", "gram_explain"):
            picks = list(dict.fromkeys(words))[:5]
            out[t] = {"marks": [{"text": w, "wrong": i == 3, "target": "[가짜] 대상", "note": f"[가짜] {w} 어법 설명", "fix": "[가짜] 맞는 설명"} for i, w in enumerate(picks)], "explain": "[가짜] 해설"}
        elif t == "link2":
            sents = re.split(r"(?<=[.!?])\s+", passage)
            if len(sents) >= 3:
                sents[1], sents[2] = "(A) " + sents[1], "(B) " + sents[2]
            out[t] = {"passage": " ".join(sents), "choices": [["However", "For example"], ["Therefore", "In addition"], ["Instead", "Thus"], ["Besides", "Still"], ["Yet", "Hence"]], "answer": 1, "explain": "[가짜] 해설"}
        elif t == "sa_arrange":
            sents = re.split(r"(?<=[.!?])\s+", passage)
            sent = max(sents, key=lambda x: -abs(len(x.split()) - 12))
            out[t] = {"sentence": sent, "korean": "[가짜] 그 문장의 우리말 뜻", "point": "[가짜] 구문", "explain": "[가짜] 해설"}
        elif t == "sa_summary":
            out[t] = {"summary": "The passage says (A) matters for (B).", "answers": [words[0], words[1]], "explain": "[가짜] 해설"}
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


def _mock(prompt: str) -> dict:
    """동형 모의고사 가짜 출제: 원래 문항 번호·종류 그대로, 새 지문 앞부분을 지문으로."""
    import json as _json

    m = re.search(r"원래 문항:\n(\[.*?\n\])\n", prompt, re.S)
    try:
        orig = _json.loads(m.group(1)) if m else []
    except ValueError:
        orig = []
    pm = re.search(r"새 지문 \(출처: [^)]*\):\n(.+?)\n\n출력", prompt, re.S)
    text = pm.group(1).strip() if pm else ""
    sents = _sentences(text)[:6] or ["This is a fake sentence for testing."]
    items = []
    for k, o in enumerate(orig):
        sa = o.get("kind") == "서술형"
        passage = ""
        if text and k == 0:
            passage = " ".join(f"{'①②③④⑤'[i]} [u]{s}[/u]" if i < 5 else s for i, s in enumerate(sents))
            if "빈칸" in (o.get("stem") or ""):
                passage += " _____ ."
        elif not text:
            passage = "[가짜] She _____ the book yesterday."
        items.append({"label": o.get("label"), "stem": o.get("stem") or "[가짜 발문]", "passage": passage,
                      "box": "[가짜] 보기 상자" if o.get("box") else "", "conditions": "(1) [가짜 조건]" if sa else "",
                      "choices": [] if sa else [f"[가짜] 보기 {i + 1}" for i in range(5)], "answer": 0 if sa else 1 + k % 5,
                      "answer_text": "[가짜] 모범 답안 문장입니다." if sa else "", "grading": "[가짜] 앞 구절 2점, 뒤 구절 3점" if sa else "",
                      "explain": "[가짜 해설]", "point": "[가짜 포인트]", "fixed": False})
    return {"change": "[가짜] 원문 그대로 싣고 밑줄 ①~⑤ 표시", "items": items}


def _summary(prompt: str) -> dict:
    sents = re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M)
    rows = []
    for n, sent in sents:
        w = sent.split()
        en = [" ".join(w[i:i + 4]) for i in range(0, len(w), 4)]
        rows.append({"n": int(n), "en": en, "ko": [f"[가짜] 덩어리{k + 1}" for k in range(len(en))]})
    words = sorted({x.lower() for x in re.findall(r"[A-Za-z]{7,}", prompt)})[:14]
    return {"title": "A Fake Title for Testing", "topic": "[가짜] 지문의 주제라는 주제",
            "purpose": "이 글은 [가짜] 내용을 설명하기 위한 것이다. 또한, 시험용임을 전달하고자 한다.",
            "summary": ["[가짜] 첫째 줄 요약.", "[가짜] 둘째 줄 요약.", "[가짜] 셋째 줄 요약."],
            "translation": "[가짜 해석] " + "자연스러운 우리말 해석이 여기 들어갑니다. " * max(3, len(rows)),
            "vocab": [{"word": w, "pos": "명", "meaning": "[가짜] 뜻"} for w in words], "sentences": rows}


def _textbook(prompt: str) -> dict:
    sents = re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M)
    rows, odd = [], []
    for n, sent in sents:
        w = sent.split()
        long = [x.strip(".,") for x in w if len(x.strip(".,")) >= 6][:2]
        verbs = [x for x in w if x in ("is", "are", "was", "were", "has", "have", "can", "will")][:1]
        rows.append({"n": int(n), "ko": f"[가짜 해석] {n}번 문장은 핵심어와 내용을 담고 있다.", "en_blanks": long, "ko_blanks": ["핵심어", "내용"],
                     "verbs": [{"text": v, "base": "be"} for v in verbs], "grammar": [{"text": v, "wrong": v + "x"} for v in verbs],
                     "chunks": [" ".join(w[i:i + 3]) for i in range(0, len(w), 3)], "keywords": [x.lower() for x in long]})
        if len(odd) < 3 and long:
            odd.append({"n": int(n), "text": long[0], "wrong": "WRONG"})
    out = {"sentences": rows}
    if '"odd"' in prompt:
        out["odd"] = odd
    return out


def _final(prompt: str) -> dict:
    """직전대비 가짜 답: 본문 [번호] 문장에서 바로 뽑음."""
    sents = re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M)
    ss = [t for _, t in sents]
    words = []
    for t in ss:
        for w in re.findall(r"[A-Za-z]{7,}", t):
            if w.lower() not in [x.lower() for x in words]:
                words.append(w)
    blanks = []
    for i, t in enumerate(ss, 1):
        w = max(re.findall(r"[A-Za-z]{4,}", t) or ["the"], key=len)
        blanks.append({"n": i, "answer": w})
    fix = []
    for i, t in enumerate(ss[1:], 2):
        m = re.search(r"\b(is|are|was|were|has|have)\b", t)
        if m and len(fix) < 3:
            sw = {"is": "are", "are": "is", "was": "were", "were": "was", "has": "have", "have": "has"}[m[1]]
            fix.append({"n": i, "wrong": t[:m.start()] + sw + t[m.end():], "error": sw, "fix": m[1], "why": "[가짜] 주어와 수 일치"})
    arrange = []
    for i, t in enumerate(ss, 1):
        ws = t.rstrip(".").split()
        if 8 <= len(ws) <= 16 and len(arrange) < 2:
            k = len(ws) // 4
            arrange.append({"n": i, "ko": f"[가짜] {i}번 문장 해석", "chunks": [" ".join(ws[j * k:(j + 1) * k if j < 3 else None]) for j in range(4)]})
    return {"topic_ko": "[가짜] 글의 주제", "translations": [f"[가짜] {i}번 문장 해석" for i in range(1, len(ss) + 1)],
            "vocab": [{"word": w, "meaning": f"[가짜] {w.lower()} 뜻"} for w in words[:12]],
            "ox": [{"ko": f"[가짜] 내용 진술 {i}", "answer": "OX"[i % 2], "why": "[가짜] 바른 내용" if i % 2 else ""} for i in range(5)],
            "topic": {"choices": ["the hidden cost of cheap products", "how to save money", "history of fashion", "new technology", "healthy diets"], "answer": 1},
            "blanks": blanks[:9], "fix": fix, "arrange": arrange,
            "compose": {"n": 1, "ko": "[가짜] 영작할 우리말", "conditions": ["[가짜] 조건 1", "[가짜] 조건 2", "[가짜] 조건 3"], "answer": ss[0] if ss else "", "grading": "[가짜] 채점 기준"},
            "insert_n": max(2, len(ss) // 2 + 1)}
