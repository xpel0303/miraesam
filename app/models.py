"""DB 모델.

0단계(기반)에서 만드는 테이블:
  academies  학원 (이름·로고·대표색·선생님 초대 코드)
  teachers   선생님 계정 (아이디+PIN, 개인 Gemini 키)
  schools    학교
  class_groups 반
  students   학생 (학생 접속 코드 포함)
  jobs       작업 대기열 (서버가 재시작돼도 이어서 실행)
  stored_files 만든 PDF 등 결과 파일 (무료 서버 디스크가 지워지므로 DB에 보관)

1단계 이후(기출 분석 등)의 테이블은 이 위에 추가합니다.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc)


class Academy(Base):
    __tablename__ = "academies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)  # data URL
    brand_color: Mapped[str] = mapped_column(String(9), default="#F2846B")
    invite_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    teachers: Mapped[list["Teacher"]] = relationship(back_populates="academy")


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    login_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(40))
    pin_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(10), default="teacher")  # owner | teacher
    gemini_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    academy: Mapped[Academy] = relationship(back_populates="teachers")


class School(Base):
    __tablename__ = "schools"
    __table_args__ = (UniqueConstraint("academy_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(String(4), default="고")  # 초 | 중 | 고
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ClassGroup(Base):
    __tablename__ = "class_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    schedule: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    teacher: Mapped[Teacher | None] = relationship()


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    school_id: Mapped[int | None] = mapped_column(ForeignKey("schools.id"), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(6), nullable=True)  # 중1~고3
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class_groups.id"), nullable=True)
    parent_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    access_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    school: Mapped[School | None] = relationship()
    class_group: Mapped[ClassGroup | None] = relationship()


class StoredFile(Base):
    __tablename__ = "stored_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(80))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workbook(Base):
    """내용이해 워크북 한 권 (지문 최대 6개).

    passages: [{title, target_grammar, passage, passage_ko, ox_ko_short[...], ..., vocab[...],
                status: waiting|making|done|failed, error, warnings[]}]
    options:  {start_number, show_grammar, show_translation, split: [6, 4]}
    """

    __tablename__ = "workbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    passages: Mapped[list] = mapped_column(JSON, default=list)
    problem_file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    answer_file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()


class AnalysisSet(Base):
    """지문 분석본 한 묶음 (지문 최대 6개 → PDF 한 파일).

    passages: [{title, passage, level, sentences_src[...], status, error, warnings[],
                title_en, title_ko, sentences[...], summary{...}, vocab[...]}]
    options:  {layout: "split" | "single", show_background, show_sketch, show_vocab}
    """

    __tablename__ = "analysis_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    passages: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()


class WordList(Base):
    """단어장. words: [{word, pos, meaning, syn, ant, example, example_ko}]"""

    __tablename__ = "word_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    source_kind: Mapped[str] = mapped_column(String(10), default="passage")  # passage | list
    source_text: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(20), default="")
    words: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # waiting|making|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()
    classes: Mapped[list["ClassGroup"]] = relationship(secondary="word_list_classes")


class WordListClass(Base):
    """단어장을 어느 반에 내줬는지."""

    __tablename__ = "word_list_classes"

    list_id: Mapped[int] = mapped_column(ForeignKey("word_lists.id", ondelete="CASCADE"), primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuizResult(Base):
    """학생이 푼 단어 퀴즈 한 번. wrong: [{word, answer, given}]"""

    __tablename__ = "quiz_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("word_lists.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(12))  # mc_en (영→한 객관식) | mc_ko | write_ko | write_en
    total: Mapped[int] = mapped_column(Integer)
    correct: Mapped[int] = mapped_column(Integer)
    wrong: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    student: Mapped["Student"] = relationship()


class GrammarSet(Base):
    """어법 문제 한 세트.

    options:   {counts: {paren: 6, ...}, under5: bool, count: bool, level, show_grammar}
    passages:  [지문 텍스트, ...] (지문형 문제용, 최대 3개)
    questions: [{type, ...문항 필드, explain}]  — 순서대로 번호가 매겨짐
    """

    __tablename__ = "grammar_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(120), default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    passages: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10), default="waiting")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()


class VariantSet(Base):
    """변형문제 한 세트. passages: [{title, text}], options: {types: [...], level}, questions: [{type, p(지문 번호), ...}]"""

    __tablename__ = "variant_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    passages: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10), default="waiting")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()


class SentenceSet(Base):
    """문장 시험지. passages: [{title, text, sentences[], rows[{n, en, ko, keys}], status, error, warnings}]
    options: {types, shuffle, hint: none|count|first, level, seed}"""

    __tablename__ = "sentence_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    passages: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()


class ParaphraseSet(Base):
    """지문 변형. passages: [{title, text, strength, out, notes[], ratio, status, error, warnings}]
    options: {level, focus, show_compare}"""

    __tablename__ = "paraphrase_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    passages: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()


class Exam(Base):
    """채점·리포트용 시험 한 회 (학교 내신, 학원 모의고사 등).

    items:   [{n, kind: mc|sa, answer, points, type, source}]  — reports/spec.py 참고
    options: {open: 학생 답 제출 받기, reveal: 제출 후 학생에게 점수 보여주기}
    """

    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    school_id: Mapped[int | None] = mapped_column(ForeignKey("schools.id"), nullable=True)
    exam_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    items: Mapped[list] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()
    school: Mapped["School | None"] = relationship()
    classes: Mapped[list["ClassGroup"]] = relationship(secondary="exam_classes")


class ExamClass(Base):
    __tablename__ = "exam_classes"

    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id", ondelete="CASCADE"), primary_key=True)


class ExamResponse(Base):
    """학생 한 명의 답안. answers {"1": "3"}, sa_scores {"21": 4.5}, sa_text {"21": "..."}"""

    __tablename__ = "exam_responses"
    __table_args__ = (UniqueConstraint("exam_id", "student_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    sa_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    sa_text: Mapped[dict] = mapped_column(JSON, default=dict)
    comment: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(10), default="teacher")  # teacher | student
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    student: Mapped["Student"] = relationship()


class Homework(Base):
    """숙제 한 건 (할 일 여러 개).

    tasks: [{kind: vocab|ox|omr|check, title, ...}]  — homework/spec.py 참고.
           채점에 필요한 정답(OX 문항, 답 입력 정답)은 만들 때 복사해 두므로 원본 자료를 고쳐도 숙제 채점은 그대로입니다.
    options: {reveal: 제출 후 정답·해설 보여주기}
    """

    __tablename__ = "homeworks"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    due: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    memo: Mapped[str] = mapped_column(Text, default="")
    tasks: Mapped[list] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()
    classes: Mapped[list["ClassGroup"]] = relationship(secondary="homework_classes")


class HomeworkClass(Base):
    __tablename__ = "homework_classes"

    homework_id: Mapped[int] = mapped_column(ForeignKey("homeworks.id", ondelete="CASCADE"), primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id", ondelete="CASCADE"), primary_key=True)


class HomeworkResult(Base):
    """학생 한 명 × 할 일 하나의 결과. 다시 풀면 attempts 가 늘고 best 는 최고 점수(%)."""

    __tablename__ = "homework_results"
    __table_args__ = (UniqueConstraint("homework_id", "student_id", "task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    homework_id: Mapped[int] = mapped_column(ForeignKey("homeworks.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    task: Mapped[int] = mapped_column(Integer)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    best: Mapped[int] = mapped_column(Integer, default=0)  # 최고 점수 %
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False)  # 통과(또는 체크) 여부
    late: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[list] = mapped_column(JSON, default=list)  # 마지막 시도의 틀린 문항
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ExamPaper(Base):
    """기출 분석: 학교 시험지 한 회.

    pages:     [{file_id, rot, w, h}]  쪽 이미지 (방향 보정된 JPEG)
    chunks:    {"0": {...Gemini 답}, "2": {...}}  2쪽씩 뽑은 원본 (재시작하면 이어서)
    questions: [{label, n, kind, points, stem, group, passage, box, choices[], conditions, type, point, pages[]}]
    meta:      {grade, year, semester, term, subject, date, ...} 시험지 머리말에서 읽은 것
    """

    __tablename__ = "exam_papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120))
    school_id: Mapped[int | None] = mapped_column(ForeignKey("schools.id"), nullable=True)
    grade: Mapped[str] = mapped_column(String(6), default="")  # 고1
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 | 2
    term: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1차 | 2차
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    pages: Mapped[list] = mapped_column(JSON, default=list)
    chunks: Mapped[dict] = mapped_column(JSON, default=dict)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # waiting|making|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    report_exam_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    teacher: Mapped["Teacher | None"] = relationship()
    school: Mapped["School | None"] = relationship()


class Job(Base):
    """오래 걸리는 작업(Gemini 호출, PDF 생성)을 맡기는 대기열.

    status: queued → running → done | failed
    step:   화면에 보여줄 현재 단계 이름 (예: "2/6쪽 읽는 중")
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    academy_id: Mapped[int] = mapped_column(ForeignKey("academies.id"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)
    step: Mapped[str] = mapped_column(String(200), default="대기 중")
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0~100
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
