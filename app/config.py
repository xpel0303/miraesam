"""환경 설정. 배포 환경에서는 환경변수로 덮어씁니다."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

APP_NAME = os.getenv("APP_NAME", "수업 도구함")

# 세션 쿠키 서명 + Gemini 키 암호화에 쓰는 비밀값. 배포 시 반드시 바꿀 것.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Postgres 주소가 있으면 Postgres, 없으면 로컬 sqlite 파일.
# (Render 무료 서버는 재시작 시 파일이 지워지므로 배포 시에는 Postgres 필수)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR.parent / 'local.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

DEFAULT_GEMINI_MODEL = os.getenv("DEFAULT_GEMINI_MODEL", "gemini-3.7-flash")
GEMINI_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]

# 작업 대기열: 동시에 돌릴 작업 수 (Gemini 무료 키 분당 한도 때문에 기본 1)
JOB_WORKERS = int(os.getenv("JOB_WORKERS", "1"))
