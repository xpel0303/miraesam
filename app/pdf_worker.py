"""PDF 그리기만 하는 작은 모듈 — 따로 띄운 프로세스에서 불러 씀.

WeasyPrint는 큰 PDF를 그릴 때 메모리를 많이 쓰고, 쓴 메모리를 서버 프로세스에 돌려주지 않음.
그래서 그리는 일은 잠깐 띄운 프로세스가 하고 끝나면 사라지게 해서, 무료 서버(512MB)에서도 지문 30개를 그릴 수 있게 함.
(이 모듈은 app 의 다른 모듈을 불러오지 않아서 새 프로세스가 빨리 뜸)
"""


def render(html: str, base_url: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html, base_url=base_url).write_pdf()


def count_pages(htmls: list[str], base_url: str) -> list[int]:
    """여러 HTML의 쪽 수만 셈 (PDF는 만들지 않음) — 한 쪽에 맞는 글자 크기를 고를 때."""
    from weasyprint import HTML

    out = []
    for h in htmls:
        doc = HTML(string=h, base_url=base_url).render()
        out.append(len(doc.pages))
        del doc
    return out


def first_fit(groups: list[list[str]], base_url: str) -> list[int]:
    """그룹마다 (큰 글씨 → 작은 글씨 순서의 HTML들) 한 쪽에 들어가는 첫 번째(가장 큰) 것의 번호. 이분 탐색."""
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    fc = FontConfiguration()  # 글꼴을 한 번만 읽고 계속 씀 (훨씬 빠름)

    def pages(h):
        doc = HTML(string=h, base_url=base_url).render(font_config=fc)
        n = len(doc.pages)
        del doc
        return n

    out = []
    for hs in groups:
        lo, hi = 0, len(hs) - 1
        if pages(hs[hi]) > 1:
            out.append(hi)
            continue
        while lo < hi:
            mid = (lo + hi) // 2
            if pages(hs[mid]) <= 1:
                hi = mid
            else:
                lo = mid + 1
        out.append(lo)
    return out
