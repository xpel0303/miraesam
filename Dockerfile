FROM python:3.12-slim-bookworm

# WeasyPrint(PDF)에 필요한 시스템 라이브러리. 한글 글꼴(나눔고딕)은 app/static/fonts 에 들어 있음.
# tesseract-ocr(-osd): 스캔 시험지 쪽 방향 자동 보정 (기출 분석)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
        tesseract-ocr tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
