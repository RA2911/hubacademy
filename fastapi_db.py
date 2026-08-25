from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

import fastapi_config as cfg


engine = create_engine(
    cfg.database_url(),
    pool_pre_ping=True,
    pool_recycle=200,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class Program(Base):
    __tablename__ = 'programs'

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Course(Base):
    __tablename__ = 'courses'

    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id'), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    title_ar = Column(String(200))
    description = Column(Text)
    description_ar = Column(Text)
    syllabus_file = Column(String(500))
    clos_file = Column(String(500))
    agent_embed_code = Column(Text)
    simulation_link = Column(String(500))
    intro_video = Column(String(500))
    num_lessons = Column(Integer, default=0)
    level = Column(String(20))
    expertise_area = Column(String(120))
    certificate_level = Column(Integer, default=0)
    learning_hours = Column(Integer, default=0)
    slug = Column(String(220), unique=True, index=True)
    sales_copy = Column(Text)
    thumbnail_url = Column(String(500))
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False, default='USD')
    stripe_price_id = Column(String(200))
    allow_free_enrollment = Column(Boolean, default=False)
    is_featured = Column(Boolean, default=False)
    openai_api_key_override = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_published = Column(Boolean, default=False)
    # AI Course Factory
    is_ai_generated = Column(Boolean, default=False, index=True)
    generation_status = Column(String(30))   # building / ready / failed
    source_topic = Column(String(200), index=True)   # normalized topic (dedup key)
    source_level = Column(String(30))                # Beginner / Intermediate / Advanced (dedup key)

    program = relationship('Program')
    lessons = relationship('Lesson', order_by='Lesson.lesson_number', back_populates='course')


class Lesson(Base):
    __tablename__ = 'lessons'

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False, index=True)
    lesson_number = Column(Integer, nullable=False)
    module_number = Column(Integer, default=1)
    session_number = Column(Integer, default=1)
    duration_minutes = Column(Integer, default=60)
    title = Column(String(200), nullable=False)
    title_ar = Column(String(200))
    description = Column(Text)
    description_ar = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    # AI Course Factory
    generation_status = Column(String(30), default='ready')  # queued / building / ready / needs_review / failed
    content_html = Column(Text)                               # generated slide/lesson HTML
    review_notes = Column(Text)                               # AI reviewer verdict/issues

    course = relationship('Course', back_populates='lessons')


class LessonMaterial(Base):
    __tablename__ = 'lesson_materials'

    id = Column(Integer, primary_key=True)
    lesson_id = Column(Integer, ForeignKey('lessons.id'), nullable=False, index=True)
    material_type = Column(String(20), nullable=False)
    language = Column(String(2), default='en', index=True)
    file_path = Column(String(500))
    file_name = Column(String(200))
    storage_provider = Column(String(30), default='r2')
    object_key = Column(String(700), index=True)
    content_type = Column(String(120))
    size_bytes = Column(Integer)
    video_url = Column(String(500))
    video_name = Column(String(200))
    presentation_html = Column(String(500))
    upload_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    lesson = relationship('Lesson', backref='materials')


class SessionObjective(Base):
    __tablename__ = 'session_objectives'

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False, index=True)
    module_number = Column(Integer, nullable=False, index=True)
    session_number = Column(Integer, nullable=False, index=True)
    title = Column(String(200))
    objective = Column(Text)
    source = Column(String(80), default='admin')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint('course_id', 'module_number', 'session_number', name='unique_session_objective'),)

    course = relationship('Course')


class Company(Base):
    __tablename__ = 'companies'

    id = Column(Integer, primary_key=True)
    member_id = Column(String(50), index=True)
    name = Column(String(200), nullable=False)
    email = Column(String(120))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Student(Base):
    __tablename__ = 'students'

    id = Column(Integer, primary_key=True)
    student_id = Column(String(50), unique=True, index=True)
    company_id = Column(Integer, ForeignKey('companies.id'), index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    full_name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    avatar_url = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime)
    is_active = Column(Boolean, default=True)

    company = relationship('Company')


class PasswordResetToken(Base):
    __tablename__ = 'password_reset_tokens'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime)

    student = relationship('Student')


class Admin(Base):
    __tablename__ = 'admins'

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    full_name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    role = Column(String(50), default='admin')
    created_at = Column(DateTime, default=datetime.utcnow)


class Enrollment(Base):
    __tablename__ = 'enrollments'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False, index=True)
    enrolled_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    __table_args__ = (UniqueConstraint('student_id', 'course_id', name='unique_student_course'),)

    student = relationship('Student')
    course = relationship('Course')


class LessonProgress(Base):
    __tablename__ = 'lesson_progress'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, index=True)
    lesson_id = Column(Integer, ForeignKey('lessons.id'), nullable=False, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    last_accessed_at = Column(DateTime, index=True)
    time_spent_seconds = Column(Integer, default=0)
    is_completed = Column(Boolean, default=False)
    content_viewed = Column(Boolean, default=False)
    revise_viewed = Column(Boolean, default=False)
    quiz_completed = Column(Boolean, default=False)

    lesson = relationship('Lesson')

    __table_args__ = (UniqueConstraint('student_id', 'lesson_id', name='unique_student_lesson_progress'),)


class Quiz(Base):
    __tablename__ = 'quizzes'

    id = Column(Integer, primary_key=True)
    lesson_id = Column(Integer, ForeignKey('lessons.id'), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    questions_json = Column(Text, nullable=False)
    is_ai_generated = Column(Boolean, default=False)
    language = Column(String(2), default='en', index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lesson = relationship('Lesson')


class QuizAttempt(Base):
    __tablename__ = 'quiz_attempts'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, index=True)
    quiz_id = Column(Integer, ForeignKey('quizzes.id'), nullable=False, index=True)
    score = Column(Integer, nullable=False)
    answers_json = Column(Text)
    questions_snapshot_json = Column(Text)
    feedback = Column(Text)
    recommendations = Column(Text)
    attempted_at = Column(DateTime, default=datetime.utcnow)


class Settings(Base):
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Purchase(Base):
    __tablename__ = 'purchases'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False, index=True)
    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False, default='USD')
    status = Column(String(30), nullable=False, default='pending')
    provider = Column(String(30), nullable=False, default='stripe')
    provider_session_id = Column(String(255), unique=True, index=True)
    provider_payment_intent = Column(String(255), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)


class CertificateAward(Base):
    __tablename__ = 'certificate_awards'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, index=True)
    expertise_area = Column(String(120), nullable=False, index=True)
    certificate_level = Column(Integer, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    hours_completed = Column(Integer, nullable=False, default=0)
    source_course_id = Column(Integer, ForeignKey('courses.id'), index=True)
    verification_code = Column(String(80), nullable=False, unique=True, index=True)
    issued_at = Column(DateTime, default=datetime.utcnow)

    student = relationship('Student')
    source_course = relationship('Course')

    __table_args__ = (UniqueConstraint('student_id', 'expertise_area', 'certificate_level', name='unique_student_expertise_certificate'),)


class LearnerProfile(Base):
    """One saved learning-capacity profile per student (retakeable — overwritten
    on each new assessment). Signals and derived scores are stored as JSON."""
    __tablename__ = 'learner_profiles'

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False, unique=True, index=True)
    topic = Column(String(200))
    scores_json = Column(Text)          # capacity scores (knowledge, reasoning, ...)
    responses_json = Column(Text)       # raw per-task signals captured in the browser
    strategy_key = Column(String(50))   # chosen archetype key
    strategy_json = Column(Text)        # archetype name/tagline/knobs + rationale
    level_band = Column(String(30))     # capacity band: Beginner / Intermediate / Advanced
    topic_band = Column(String(30))     # topic-knowledge placement: Beginner / Intermediate / Advanced
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    student = relationship('Student')


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


COURSE_COLUMNS = {
    'slug': "VARCHAR(220)",
    'sales_copy': "TEXT",
    'thumbnail_url': "VARCHAR(500)",
    'price_cents': "INTEGER NOT NULL DEFAULT 0",
    'currency': "VARCHAR(3) NOT NULL DEFAULT 'USD'",
    'stripe_price_id': "VARCHAR(200)",
    'allow_free_enrollment': "BOOLEAN DEFAULT FALSE",
    'is_featured': "BOOLEAN DEFAULT FALSE",
    'expertise_area': "VARCHAR(120)",
    'certificate_level': "INTEGER DEFAULT 0",
    'learning_hours': "INTEGER DEFAULT 0",
    'is_ai_generated': "BOOLEAN DEFAULT FALSE",
    'generation_status': "VARCHAR(30)",
    'source_topic': "VARCHAR(200)",
    'source_level': "VARCHAR(30)",
}

MATERIAL_COLUMNS = {
    'storage_provider': "VARCHAR(30) DEFAULT 'r2'",
    'object_key': "VARCHAR(700)",
    'content_type': "VARCHAR(120)",
    'size_bytes': "INTEGER",
}

LESSON_COLUMNS = {
    'module_number': "INTEGER DEFAULT 1",
    'session_number': "INTEGER DEFAULT 1",
    'duration_minutes': "INTEGER DEFAULT 60",
    'generation_status': "VARCHAR(30) DEFAULT 'ready'",
    'content_html': "TEXT",
    'review_notes': "TEXT",
}

QUIZ_ATTEMPT_COLUMNS = {
    'questions_snapshot_json': "TEXT",
    'feedback': "TEXT",
    'recommendations': "TEXT",
}

LEARNER_PROFILE_COLUMNS = {
    'topic_band': "VARCHAR(30)",
}


def ensure_schema():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if 'courses' not in inspector.get_table_names():
        return
    existing = {column['name'] for column in inspector.get_columns('courses')}
    with engine.begin() as conn:
        for name, ddl in COURSE_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE courses ADD COLUMN {name} {ddl}"))
    if 'lesson_materials' in inspector.get_table_names():
        existing_material = {column['name'] for column in inspector.get_columns('lesson_materials')}
        with engine.begin() as conn:
            for name, ddl in MATERIAL_COLUMNS.items():
                if name not in existing_material:
                    conn.execute(text(f"ALTER TABLE lesson_materials ADD COLUMN {name} {ddl}"))
    if 'lessons' in inspector.get_table_names():
        existing_lesson = {column['name'] for column in inspector.get_columns('lessons')}
        with engine.begin() as conn:
            for name, ddl in LESSON_COLUMNS.items():
                if name not in existing_lesson:
                    conn.execute(text(f"ALTER TABLE lessons ADD COLUMN {name} {ddl}"))
    if 'quiz_attempts' in inspector.get_table_names():
        existing_attempt = {column['name'] for column in inspector.get_columns('quiz_attempts')}
        with engine.begin() as conn:
            for name, ddl in QUIZ_ATTEMPT_COLUMNS.items():
                if name not in existing_attempt:
                    conn.execute(text(f"ALTER TABLE quiz_attempts ADD COLUMN {name} {ddl}"))
    if 'learner_profiles' in inspector.get_table_names():
        existing_lp = {column['name'] for column in inspector.get_columns('learner_profiles')}
        with engine.begin() as conn:
            for name, ddl in LEARNER_PROFILE_COLUMNS.items():
                if name not in existing_lp:
                    conn.execute(text(f"ALTER TABLE learner_profiles ADD COLUMN {name} {ddl}"))
