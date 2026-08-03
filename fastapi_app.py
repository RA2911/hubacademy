import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import smtplib
import uuid
from io import BytesIO
from datetime import datetime, timedelta
from email.message import EmailMessage

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

import fastapi_config as cfg
from fastapi_auth import admin_from_request, hash_password, username_from_email, verify_password
from fastapi_db import (Admin, CertificateAward, Company, Course, Enrollment, Lesson, LessonMaterial, LessonProgress,
                        PasswordResetToken, Program, Purchase, Quiz, QuizAttempt, SessionObjective, Settings, Student,
                        db_session as next_db_session, ensure_schema, get_db)
from fastapi_storage import (guess_content_type, list_objects, object_bytes, object_key, package_object_key,
                             presigned_download_url, presigned_upload_url, r2_enabled, upload_fileobj)


CERTIFICATE_LEVEL_HOURS = 15
MODULES_PER_LEVEL = 5
SESSIONS_PER_MODULE = 5
SESSION_DURATION_MINUTES = 180
MASTER_CERTIFICATE_LEVEL = 4
PASSWORD_RESET_TOKEN_MINUTES = 60
MAX_LESSON_QUIZ_ATTEMPTS = 3
QUIZ_PASS_SCORE = 60

MATERIAL_TYPE_OPTIONS = [
    ('slide', 'Slides / PPT / PDF'),
    ('html', 'HTML lessons / interactive slides'),
    ('module_intro_video', 'Module intro videos'),
    ('video', 'Videos'),
    ('case_application', 'Case applications'),
    ('case_study', 'Case studies'),
    ('case_analysis', 'Module case analysis'),
    ('toolkit', 'Toolkits'),
    ('toolkit_asset', 'Toolkit Excel/assets'),
    ('simulation', 'Simulations'),
    ('general_simulation', 'Module general simulation'),
    ('syllabus', 'Syllabus'),
    ('clo', 'CLO / CBO'),
    ('article', 'Articles / readings'),
    ('other', 'Other material'),
]
MATERIAL_TYPE_KEYS = {key for key, _label in MATERIAL_TYPE_OPTIONS}

EXPERTISE_AREAS = [
    {'name': 'AI Agents & Generative AI', 'slug': 'ai-agents-generative-ai'},
    {'name': 'Data Analytics & Business Intelligence', 'slug': 'data-analytics-bi'},
    {'name': 'Cybersecurity', 'slug': 'cybersecurity'},
    {'name': 'Cloud, DevOps & Infrastructure', 'slug': 'cloud-devops-infrastructure'},
    {'name': 'Automation & Workflow Design', 'slug': 'automation-workflow-design'},
    {'name': 'Digital Transformation Leadership', 'slug': 'digital-transformation-leadership'},
    {'name': 'Sustainability & Green Business', 'slug': 'sustainability-green-business'},
]

CERTIFICATE_LEVELS = [
    {'level': 1, 'name': 'Level 1 Certificate', 'hours': CERTIFICATE_LEVEL_HOURS, 'components': ['videos', 'practices', 'simulations', 'evaluation']},
    {'level': 2, 'name': 'Level 2 Certificate', 'hours': CERTIFICATE_LEVEL_HOURS, 'components': ['videos', 'practices', 'simulations', 'evaluation']},
    {'level': 3, 'name': 'Level 3 Certificate', 'hours': CERTIFICATE_LEVEL_HOURS, 'components': ['videos', 'practices', 'simulations', 'evaluation']},
    {'level': MASTER_CERTIFICATE_LEVEL, 'name': 'Master Certificate', 'hours': CERTIFICATE_LEVEL_HOURS * 3, 'components': ['Level 1 completion', 'Level 2 completion', 'Level 3 completion']},
]


cfg.validate_production()
app = FastAPI(title='Hub Academy')
app.add_middleware(
    SessionMiddleware,
    secret_key=cfg.SECRET_KEY,
    same_site='lax',
    https_only=os.environ.get('ENV') == 'production' or os.environ.get('APP_ENV') == 'production',
)
app.mount('/static', StaticFiles(directory=os.path.join(cfg.BASE_DIR, 'static')), name='static')

templates = Jinja2Templates(directory=os.path.join(cfg.BASE_DIR, 'fastapi_templates'))
logger = logging.getLogger(__name__)


@app.on_event('startup')
def startup():
    ensure_schema()


@app.get('/healthz')
def healthz():
    return {'ok': True}


@app.get('/_build-check')
def build_check():
    lesson_template = os.path.join(cfg.BASE_DIR, 'fastapi_templates', 'learn', 'lesson.html')
    try:
        with open(lesson_template, 'r', encoding='utf-8') as handle:
            text = handle.read()
    except OSError:
        text = ''
    return {
        'commit_marker': 'aef1119-fa5ca6e',
        'lesson_workspace': 'lesson-workspace' in text,
        'ai_tools': 'AI Tools & Applications' in text,
        'self_evaluation': 'Self-Evaluation' in text,
        'service_worker_v2': True,
    }


@app.get('/manifest.webmanifest')
def pwa_manifest():
    return FileResponse(os.path.join(cfg.BASE_DIR, 'static', 'manifest.webmanifest'), media_type='application/manifest+json')


@app.get('/service-worker.js')
def service_worker():
    return FileResponse(os.path.join(cfg.BASE_DIR, 'static', 'service-worker.js'), media_type='application/javascript')


def slugify(value):
    value = re.sub(r'[^a-zA-Z0-9]+', '-', value.lower()).strip('-')
    return value or 'course'


def course_slug(course):
    return course.slug or f'{course.id}-{slugify(course.title)}'


def course_price(course):
    amount = course.price_cents or 0
    if amount <= 0 or course.allow_free_enrollment:
        return 'Free'
    return f"{(course.currency or 'USD').upper()} {amount / 100:.2f}"


def module_number_for_session(lesson_number: int):
    return int((max(lesson_number, 1) - 1) / SESSIONS_PER_MODULE) + 1


def session_number_for_lesson(lesson_number: int):
    return ((max(lesson_number, 1) - 1) % SESSIONS_PER_MODULE) + 1


def lesson_module_number(lesson):
    return lesson.module_number or module_number_for_session(lesson.lesson_number)


def lesson_session_number(lesson):
    return lesson.session_number or session_number_for_lesson(lesson.lesson_number)


def lesson_duration_minutes(lesson):
    return lesson.duration_minutes or SESSION_DURATION_MINUTES


def lesson_has_content(db: Session, lesson_id: int) -> bool:
    return bool(
        db.query(LessonMaterial).filter_by(lesson_id=lesson_id).count()
        or db.query(LessonProgress).filter_by(lesson_id=lesson_id).count()
        or db.query(Quiz).filter_by(lesson_id=lesson_id).count()
    )


def normalize_course_modules(db: Session, course: Course, module_count: int = MODULES_PER_LEVEL):
    lessons = db.query(Lesson).filter_by(course_id=course.id).order_by(Lesson.lesson_number).all()
    for number in range(len(lessons) + 1, module_count + 1):
        lesson = Lesson(course_id=course.id, lesson_number=number, created_at=datetime.utcnow())
        db.add(lesson)
        lessons.append(lesson)
    blocked_extras = []
    for index, lesson in enumerate(lessons, start=1):
        if index <= module_count:
            lesson.lesson_number = index
            lesson.module_number = index
            lesson.session_number = 1
            lesson.duration_minutes = SESSION_DURATION_MINUTES
            if re.match(r'^Module\s+\d+(\s*[-–]\s*Session\s+\d+)?$', lesson.title or '', flags=re.IGNORECASE):
                lesson.title = f'Module {index}'
            elif not (lesson.title or '').strip():
                lesson.title = f'Module {index}'
        elif lesson_has_content(db, lesson.id):
            blocked_extras.append(lesson.title or f'Lesson {lesson.lesson_number}')
        else:
            db.delete(lesson)
    if blocked_extras:
        return blocked_extras
    course.num_lessons = module_count
    return []


def material_display_label(material, index):
    material_type = material.material_type or 'other'
    if material_type in ('html', 'slide'):
        return f'Session {index}'
    if material_type == 'module_intro_video':
        return 'Module intro video'
    if material_type == 'video':
        return f'Video {index}'
    if material_type == 'simulation':
        return f'Simulation {index}'
    if material_type == 'general_simulation':
        return 'General simulation'
    if material_type == 'case_analysis':
        return 'Case analysis'
    if material_type in ('toolkit', 'toolkit_asset'):
        return f'Application {index}'
    if material_type in ('case_application', 'case_study'):
        return f'Case {index}'
    if material_type == 'syllabus':
        return 'Syllabus'
    if material_type == 'clo':
        return 'CLO / CBO'
    return f'Material {index}'


def group_lesson_materials(materials):
    groups = {
        'learning': [],
        'ai_tools': [],
        'support': [],
    }
    counts = {}
    for material in materials:
        material_type = material.material_type or 'other'
        counts[material_type] = counts.get(material_type, 0) + 1
        item = {
            'material': material,
            'label': material_display_label(material, counts[material_type]),
        }
        if material_type in ('html', 'slide', 'video', 'simulation'):
            groups['learning'].append(item)
        elif material_type in ('toolkit', 'toolkit_asset', 'case_application', 'case_study'):
            groups['ai_tools'].append(item)
        else:
            groups['support'].append(item)
    return groups


def module_material_groups(materials, module_number):
    groups = {
        'sessions': [],
        'videos': [],
        'simulations': [],
        'applications': [],
        'cases': [],
        'references': [],
    }
    counters = {key: 0 for key in groups}
    for material in materials:
        material_type = material.material_type or 'other'
        if material_type == 'toolkit_asset':
            continue
        if material_type in ('html', 'slide'):
            group = 'sessions'
            label = f'Session {module_number}'
        elif material_type == 'video':
            group = 'videos'
            label = f'Video {module_number}'
        elif material_type == 'simulation':
            group = 'simulations'
            label = f'Simulation {module_number}'
        elif material_type == 'toolkit' and html_material(material):
            group = 'applications'
            label = f'Application {module_number}'
        elif material_type in ('case_application', 'case_study'):
            group = 'cases'
            label = f'Case {module_number}'
        else:
            group = 'references'
            counters[group] += 1
            label = material_display_label(material, counters[group])
        groups[group].append({'material': material, 'label': label})
    return groups


def material_unit_number(material):
    file_text = ' '.join([
        material.file_name or '',
        material.video_name or '',
    ])
    path_text = ' '.join([
        material.object_key or '',
        material.file_path or '',
    ])
    text = f'{file_text} {path_text}'

    folder_match = re.search(r'(?:^|[/\\])0*(\d+)[-_ ]session(?:[_-]\d+min)?(?:[/\\]|$)', text, flags=re.IGNORECASE)
    if folder_match:
        try:
            number = int(folder_match.group(1))
            if number > 0:
                return number
        except ValueError:
            pass

    filename_patterns = [
        r'(?:session|simulation|sim|vol|application|case)[-_ ]*0*(\d+)',
        r'(?:^|[_\W])module[-_ ]*0*(\d+)',
        r'[_-]0*(\d+)[_-](?:ar[_-])?audio',
    ]
    for pattern in filename_patterns:
        match = re.search(pattern, file_text, flags=re.IGNORECASE)
        if match:
            try:
                number = int(match.group(1))
                if number > 0:
                    return number
            except ValueError:
                continue

    # Bulk imports store packages such as module-42-session-3-html.
    # This must not treat 01_Session_30min as session 30.
    session_match = re.search(r'(?:^|[/_-])session[-_ ]*0*(\d+)(?:[-_/]|$)', path_text, flags=re.IGNORECASE)
    if session_match:
        try:
            number = int(session_match.group(1))
            if number > 0:
                return number
        except ValueError:
            pass

    fallback_patterns = [
        r'(?:session|simulation|sim|vol|application|case)[-_ ]*0*(\d+)',
        r'(?:^|[_\W])module[-_ ]*0*(\d+)',
        r'[_-]0*(\d+)[_-](?:ar[_-])?audio',
        r'[_-]0*(\d+)[_-][a-z]',
        r'[_-]0*(\d+)\.',
    ]
    for pattern in fallback_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                number = int(match.group(1))
                if number > 0:
                    return number
            except ValueError:
                continue
    return None


def module_session_groups(materials, module_number, objective_map=None):
    objective_map = objective_map or {}
    sessions = {}
    fallback_counts = {'session': 0, 'video': 0, 'simulation': 0, 'application': 0, 'case': 0}

    def session_entry(unit):
        sessions.setdefault(unit, {
            'unit': unit,
            'label': f'{module_number}.{unit}',
            'objective': objective_map.get((module_number, unit)) or 'Review the session resources, apply the activity, and prepare for the module evaluation.',
            'session': [],
            'video': [],
            'simulation': [],
            'application': [],
            'case': [],
        })
        return sessions[unit]

    for material in materials:
        material_type = material.material_type or 'other'
        if material_type in ('toolkit_asset', 'module_intro_video', 'case_analysis', 'general_simulation'):
            continue
        if material_type in ('html', 'slide'):
            bucket = 'session'
        elif material_type == 'video':
            bucket = 'video'
        elif material_type == 'simulation':
            bucket = 'simulation'
        elif material_type == 'toolkit' and html_material(material):
            bucket = 'application'
        elif material_type in ('case_application', 'case_study'):
            bucket = 'case'
        else:
            continue
        unit = material_unit_number(material)
        if not unit:
            fallback_counts[bucket] += 1
            unit = fallback_counts[bucket]
        session_entry(unit)[bucket].append(material)

    return [sessions[key] for key in sorted(sessions)]


def module_extra_materials(materials):
    extras = {
        'module_intro_video': [],
        'case_analysis': [],
        'general_simulation': [],
        'references': [],
    }
    for material in materials:
        material_type = material.material_type or 'other'
        if material_type in extras:
            extras[material_type].append(material)
        elif material_type in ('syllabus', 'clo'):
            extras['references'].append(material)
    return extras


def session_objective_map(db: Session, course_id: int):
    rows = db.query(SessionObjective).filter_by(course_id=course_id).all()
    return {
        (row.module_number, row.session_number): row.objective
        for row in rows
        if row.objective
    }


def current_module_number_for_lesson(db: Session, lesson: Lesson) -> int:
    lessons = db.query(Lesson).filter_by(course_id=lesson.course_id).order_by(Lesson.lesson_number).limit(lesson.course.num_lessons or MODULES_PER_LEVEL).all()
    return next((index for index, item in enumerate(lessons, start=1) if item.id == lesson.id), lesson_session_number(lesson))


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError('pypdf is required for syllabus objective extraction.') from exc
    reader = PdfReader(BytesIO(pdf_bytes))
    return '\n'.join(page.extract_text() or '' for page in reader.pages)


def extract_topic_objectives(text: str, limit: int = SESSIONS_PER_MODULE):
    lines = [line.strip() for line in (text or '').splitlines()]
    start = next((index for index, line in enumerate(lines) if re.search(r'\btopics covered\b', line, re.IGNORECASE)), -1)
    if start < 0:
        return []
    objectives = []
    stop_pattern = re.compile(r'^(applied lab|module\s+\d+|learning outcomes|note on scope)\b', re.IGNORECASE)
    for line in lines[start + 1:]:
        cleaned = re.sub(r'^[\u2022\-–—*\d\.\)\s]+', '', line).strip()
        if not cleaned:
            continue
        if stop_pattern.search(cleaned):
            break
        if len(cleaned) < 8:
            continue
        objectives.append(cleaned)
        if len(objectives) >= limit:
            break
    return objectives


def save_session_objectives(db: Session, course_id: int, module_number: int, objectives):
    saved = 0
    for index, objective in enumerate(objectives[:SESSIONS_PER_MODULE], start=1):
        existing = db.query(SessionObjective).filter_by(
            course_id=course_id,
            module_number=module_number,
            session_number=index,
        ).first()
        if not existing:
            existing = SessionObjective(course_id=course_id, module_number=module_number, session_number=index)
            db.add(existing)
        existing.objective = objective.strip()
        existing.title = None
        existing.source = 'syllabus'
        saved += 1
    db.commit()
    return saved


def module_nav_for_lessons(db: Session, student_id: int, lessons, current_lesson_id: int):
    items = []
    previous_progress = None
    for index, item in enumerate(lessons, start=1):
        progress = db.query(LessonProgress).filter_by(student_id=student_id, lesson_id=item.id).first()
        unlocked = index == 1 or session_unlocked(previous_progress)
        items.append({
            'lesson': item,
            'number': index,
            'active': item.id == current_lesson_id,
            'unlocked': unlocked,
            'completed': bool(progress and progress.is_completed),
        })
        previous_progress = progress
    return items


def lesson_context_text(lesson, materials):
    course = lesson.course
    material_lines = [
        f"- {m.material_type}: {m.file_name or m.video_name or m.object_key or m.video_url}"
        for m in materials[:40]
    ]
    return "\n".join([
        f"Course: {course.title if course else ''}",
        f"Lesson: {lesson.title}",
        f"Description: {lesson.description or ''}",
        "Available materials:",
        "\n".join(material_lines),
    ])


def openai_api_key(db: Session = None, course=None):
    if course and getattr(course, 'openai_api_key_override', None):
        return course.openai_api_key_override.strip()
    if cfg.OPENAI_API_KEY:
        return cfg.OPENAI_API_KEY
    if db:
        setting = db.query(Settings).filter_by(key='openai_api_key').first()
        if setting and setting.value:
            return setting.value.strip()
    return ''


def openai_client(db: Session = None, course=None):
    api_key = openai_api_key(db, course)
    if not api_key:
        raise RuntimeError('OpenAI API key is not configured.')
    from openai import OpenAI
    return OpenAI(api_key=api_key, timeout=45, max_retries=1)


def parse_json_response(text, fallback):
    cleaned = (text or '').strip()
    if cleaned.startswith('```json'):
        cleaned = cleaned[7:]
    if cleaned.startswith('```'):
        cleaned = cleaned[3:]
    if cleaned.endswith('```'):
        cleaned = cleaned[:-3]
    try:
        return json.loads(cleaned.strip())
    except Exception:
        return fallback


def ai_error_response(exc, fallback_message):
    message = str(exc)
    if 'OpenAI API key is not configured' in message:
        return JSONResponse({'error': 'OpenAI API key is not configured. Add it in Admin Settings or Cloud Run OPENAI_API_KEY.'}, status_code=400)
    logger.exception('%s: %s', fallback_message, exc)
    details = []
    current = exc
    while current and len(details) < 4:
        detail = str(current).strip() or current.__class__.__name__
        details.append(detail.replace('\n', ' '))
        current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
    public_detail = ' | '.join(dict.fromkeys(details))
    if len(public_detail) > 260:
        public_detail = public_detail[:257] + '...'
    return JSONResponse({'error': f'{fallback_message} OpenAI said: {public_detail}' if public_detail else fallback_message}, status_code=502)


def openai_chat_completion(client, messages, max_tokens, temperature):
    models = []
    for model in [cfg.OPENAI_MODEL, 'gpt-4o-mini', 'gpt-4.1-mini']:
        if model and model not in models:
            models.append(model)
    last_error = None
    for model in models:
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError('OpenAI chat completion failed.')


def ai_generate_quiz_questions(db, lesson, materials, previous_questions):
    prompt = f"""Create exactly 5 multiple-choice questions for this lesson.

The questions must be scenario-based, non-obvious, and specific to this lesson. Avoid generic definitions and avoid questions about file names or course structure.

Lesson context:
{lesson_context_text(lesson, materials)}

Previous questions to avoid repeating:
{json.dumps(previous_questions[-30:], ensure_ascii=False)}

Return only valid JSON:
[
  {{
    "question": "question text",
    "options": ["A", "B", "C", "D"],
    "correct_answer": 0,
    "explanation": "short explanation"
  }}
]"""
    response = openai_chat_completion(
        openai_client(db, lesson.course),
        [{'role': 'user', 'content': prompt}],
        max_tokens=1800,
        temperature=0.9,
    )
    data = parse_json_response(response.choices[0].message.content, [])
    return data[:5] if isinstance(data, list) else []


def ai_generate_feedback(db, lesson, score, questions, answers):
    prompt = f"""A student completed a quiz for this lesson.

Lesson: {lesson.title}
Score: {score}%
Questions and correct answers:
{json.dumps(questions, ensure_ascii=False)}
Student answers by question index:
{json.dumps(answers, ensure_ascii=False)}

Return only valid JSON:
{{
  "feedback": "specific feedback on strengths and mistakes",
  "recommendations": "specific next steps to improve"
}}"""
    response = openai_chat_completion(
        openai_client(db, lesson.course),
        [{'role': 'user', 'content': prompt}],
        max_tokens=700,
        temperature=0.5,
    )
    data = parse_json_response(response.choices[0].message.content, {})
    return {
        'feedback': data.get('feedback') or 'Quiz completed. Review the explanations and retry with stronger focus on weak areas.',
        'recommendations': data.get('recommendations') or 'Review the lesson materials and applications before the next attempt.',
    }


def course_quiz_stats(db: Session, student_id: int, course_id: int):
    attempts = db.query(QuizAttempt).join(Quiz, QuizAttempt.quiz_id == Quiz.id).join(
        Lesson, Quiz.lesson_id == Lesson.id
    ).filter(QuizAttempt.student_id == student_id, Lesson.course_id == course_id).all()
    if not attempts:
        return {'attempts': 0, 'average': 0, 'best': 0}
    scores = [attempt.score for attempt in attempts]
    return {'attempts': len(scores), 'average': int(sum(scores) / len(scores)), 'best': max(scores)}


def course_quiz_average_passed(db: Session, student_id: int, course_id: int):
    stats = course_quiz_stats(db, student_id, course_id)
    return stats['attempts'] > 0 and stats['average'] >= QUIZ_PASS_SCORE


def certificate_badge(course):
    level = course.certificate_level or 0
    if level in (1, 2, 3):
        hours = course.learning_hours or CERTIFICATE_LEVEL_HOURS
        return f'Level {level} certificate track - {hours} hours'
    return 'Certificate-ready'


def step_state(progress):
    return [
        {
            'number': 1,
            'key': 'learn',
            'title': 'Learn',
            'label': 'Watch and study',
            'complete': bool(progress and progress.content_viewed),
            'unlocked': True,
        },
        {
            'number': 2,
            'key': 'practice',
            'title': 'Practice',
            'label': 'Apply the concept',
            'complete': bool(progress and progress.revise_viewed),
            'unlocked': bool(progress and progress.content_viewed),
        },
        {
            'number': 3,
            'key': 'validate',
            'title': 'Validate',
            'label': 'Check and confirm',
            'complete': bool(progress and progress.quiz_completed),
            'unlocked': bool(progress and progress.content_viewed and progress.revise_viewed),
        },
    ]


def session_unlocked(previous_progress):
    return previous_progress is None or previous_progress.is_completed


def journey_for_course(db: Session, course: Course, student_id: int):
    lessons = db.query(Lesson).filter_by(course_id=course.id).order_by(Lesson.lesson_number).limit(course.num_lessons or MODULES_PER_LEVEL).all()
    progress = {p.lesson_id: p for p in db.query(LessonProgress).filter(
        LessonProgress.student_id == student_id,
        LessonProgress.lesson_id.in_([lesson.id for lesson in lessons] or [0])
    ).all()}
    modules = []
    previous_progress = None
    total_done = 0
    for lesson in lessons:
        current_progress = progress.get(lesson.id)
        if current_progress and current_progress.is_completed:
            total_done += 1
        module_number = lesson_module_number(lesson)
        while len(modules) < module_number:
            modules.append({'number': len(modules) + 1, 'sessions': []})
        modules[module_number - 1]['sessions'].append({
            'lesson': lesson,
            'progress': current_progress,
            'unlocked': session_unlocked(previous_progress),
            'session_number': lesson_session_number(lesson),
            'duration_minutes': lesson_duration_minutes(lesson),
            'steps': step_state(current_progress),
        })
        previous_progress = current_progress
    return {
        'modules': modules,
        'total_sessions': len(lessons),
        'completed_sessions': total_done,
        'progress': progress,
        'pct': int(total_done / len(lessons) * 100) if lessons else 0,
    }


def subscription_plans():
    return [
        {'name': 'Monthly', 'price': cfg.SUBSCRIPTION_MONTHLY_PRICE, 'period': 'month', 'stripe_price_id': cfg.STRIPE_MONTHLY_PRICE_ID,
         'features': ['Unlimited course access', 'AI learning guide', 'Progress tracking', 'Cancel anytime']},
        {'name': 'Annual', 'price': cfg.SUBSCRIPTION_ANNUAL_PRICE, 'period': 'year', 'stripe_price_id': cfg.STRIPE_ANNUAL_PRICE_ID,
         'features': ['Everything in Monthly', 'Best value', 'Certificates included', 'Priority support']},
    ]


def student_from_request(request: Request, db: Session):
    student_id = request.session.get('student_id')
    if not student_id:
        return None
    student = db.get(Student, int(student_id))
    if not student or not student.is_active:
        request.session.clear()
        return None
    return student


def require_admin(request: Request, db: Session):
    admin = admin_from_request(request, db)
    if not admin:
        raise HTTPException(status_code=303, headers={'Location': '/admin/login'})
    return admin


def template(request: Request, name: str, db: Session, context=None):
    ctx = {
        'request': request,
        'current_user': student_from_request(request, db),
        'current_admin': admin_from_request(request, db),
        'course_slug': course_slug,
        'course_price': course_price,
        'certificate_badge': certificate_badge,
        'lesson_module_number': lesson_module_number,
        'lesson_session_number': lesson_session_number,
        'lesson_duration_minutes': lesson_duration_minutes,
        'certificate_levels': CERTIFICATE_LEVELS,
        'expertise_areas': EXPERTISE_AREAS,
        'plans': subscription_plans(),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(name, ctx)


def categories(db: Session):
    rows = db.query(Course.expertise_area, func.count(Course.id)).filter(Course.is_published.is_(True)).group_by(Course.expertise_area).all()
    counts = {name: count for name, count in rows if name}
    return [
        {'name': area['name'], 'count': counts.get(area['name']), 'href': f"/courses?expertise={area['name']}"}
        for area in EXPERTISE_AREAS
    ]


def find_course(db: Session, identifier: str):
    course = db.query(Course).filter_by(slug=identifier, is_published=True).first()
    if course:
        return course
    prefix = identifier.split('-', 1)[0]
    if prefix.isdigit():
        return db.query(Course).filter_by(id=int(prefix), is_published=True).first()
    return None


def enroll_student(db: Session, student_id: int, course_id: int):
    enrollment = db.query(Enrollment).filter_by(student_id=student_id, course_id=course_id).first()
    if enrollment:
        enrollment.is_active = True
    else:
        db.add(Enrollment(student_id=student_id, course_id=course_id, is_active=True))


def material_access_allowed(material: LessonMaterial, request: Request, db: Session) -> bool:
    admin = admin_from_request(request, db)
    if admin:
        return True
    student = student_from_request(request, db)
    if student and material.lesson:
        return db.query(Enrollment).filter_by(
            student_id=student.id,
            course_id=material.lesson.course_id,
            is_active=True,
        ).first() is not None
    return False


def html_material(material: LessonMaterial) -> bool:
    name = (material.file_name or '').lower()
    content_type = (material.content_type or '').lower()
    return name.endswith(('.html', '.htm')) or content_type.startswith('text/html')


def uploaded_file_size(fileobj) -> int:
    try:
        current = fileobj.tell()
        fileobj.seek(0, os.SEEK_END)
        size = fileobj.tell()
        fileobj.seek(current)
        return int(size)
    except Exception:
        return 0


def infer_material_type_from_key(key: str) -> str:
    parts = key.split('/')
    if len(parts) >= 3 and parts[0] == 'lessons':
        candidate = parts[2]
        if candidate in MATERIAL_TYPE_KEYS:
            return candidate
    return 'other'


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def mail_configured() -> bool:
    return all([cfg.MAIL_SERVER, cfg.MAIL_USERNAME, cfg.MAIL_PASSWORD, cfg.MAIL_DEFAULT_SENDER])


def send_email(to_email: str, subject: str, text_body: str):
    if not mail_configured():
        raise RuntimeError('Mail is not configured.')
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = cfg.MAIL_DEFAULT_SENDER
    message['To'] = to_email
    message.set_content(text_body)

    if cfg.MAIL_USE_SSL:
        with smtplib.SMTP_SSL(cfg.MAIL_SERVER, cfg.MAIL_PORT, timeout=20) as smtp:
            smtp.login(cfg.MAIL_USERNAME, cfg.MAIL_PASSWORD)
            smtp.send_message(message)
        return

    with smtplib.SMTP(cfg.MAIL_SERVER, cfg.MAIL_PORT, timeout=20) as smtp:
        if cfg.MAIL_USE_TLS:
            smtp.starttls()
        smtp.login(cfg.MAIL_USERNAME, cfg.MAIL_PASSWORD)
        smtp.send_message(message)


def create_password_reset(db: Session, student: Student) -> str:
    token = secrets.token_urlsafe(32)
    reset = PasswordResetToken(
        student_id=student.id,
        token_hash=token_hash(token),
        expires_at=datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_TOKEN_MINUTES),
    )
    db.add(reset)
    return token


def course_is_completed(db: Session, student_id: int, course: Course):
    lessons = db.query(Lesson.id).filter_by(course_id=course.id).all()
    lesson_ids = [row[0] for row in lessons]
    if not lesson_ids:
        return False
    completed = db.query(LessonProgress).filter(
        LessonProgress.student_id == student_id,
        LessonProgress.lesson_id.in_(lesson_ids),
        LessonProgress.is_completed.is_(True),
    ).count()
    return completed == len(lesson_ids) and course_quiz_average_passed(db, student_id, course.id)


def certificate_title(expertise_area: str, certificate_level: int):
    if certificate_level == MASTER_CERTIFICATE_LEVEL:
        return f'{expertise_area} Master Certificate'
    return f'{expertise_area} Level {certificate_level} Certificate'


def award_certificate(db: Session, student_id: int, expertise_area: str, certificate_level: int, hours_completed: int, source_course_id: int = None):
    existing = db.query(CertificateAward).filter_by(
        student_id=student_id,
        expertise_area=expertise_area,
        certificate_level=certificate_level,
    ).first()
    if existing:
        return existing
    award = CertificateAward(
        student_id=student_id,
        expertise_area=expertise_area,
        certificate_level=certificate_level,
        title=certificate_title(expertise_area, certificate_level),
        hours_completed=hours_completed,
        source_course_id=source_course_id,
        verification_code=uuid.uuid4().hex,
        issued_at=datetime.utcnow(),
    )
    db.add(award)
    return award


def evaluate_certificates(db: Session, student_id: int, course: Course):
    expertise_area = course.expertise_area or (course.program.name if course.program else None)
    certificate_level = course.certificate_level or 0
    if not expertise_area or certificate_level not in (1, 2, 3):
        return []
    if not course_is_completed(db, student_id, course):
        return []

    completed_courses = db.query(Course).join(Lesson, Lesson.course_id == Course.id).join(
        LessonProgress, LessonProgress.lesson_id == Lesson.id
    ).filter(
        Course.expertise_area == expertise_area,
        Course.certificate_level == certificate_level,
        LessonProgress.student_id == student_id,
        LessonProgress.is_completed.is_(True),
    ).distinct().all()
    hours_completed = 0
    for completed_course in completed_courses:
        if course_is_completed(db, student_id, completed_course):
            hours_completed += completed_course.learning_hours or 0
    if hours_completed < CERTIFICATE_LEVEL_HOURS:
        return []

    awards = [award_certificate(db, student_id, expertise_area, certificate_level, hours_completed, course.id)]
    completed_level_awards = db.query(CertificateAward).filter(
        CertificateAward.student_id == student_id,
        CertificateAward.expertise_area == expertise_area,
        CertificateAward.certificate_level.in_([1, 2, 3]),
    ).count()
    if completed_level_awards == 3:
        awards.append(award_certificate(db, student_id, expertise_area, MASTER_CERTIFICATE_LEVEL, CERTIFICATE_LEVEL_HOURS * 3, course.id))
    return awards


@app.get('/', response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    featured = db.query(Course).filter_by(is_published=True, is_featured=True).order_by(Course.created_at.desc()).limit(6).all()
    courses = db.query(Course).filter_by(is_published=True).order_by(Course.created_at.desc()).limit(12).all()
    return template(request, 'home.html', db, {'featured': featured, 'courses': courses, 'categories': categories(db)})


@app.get('/courses', response_class=HTMLResponse)
def catalog(request: Request, q: str = '', level: str = '', expertise: str = '', db: Session = Depends(get_db)):
    query = db.query(Course).filter_by(is_published=True)
    if level:
        query = query.filter(Course.level == level)
    if expertise:
        query = query.filter(Course.expertise_area == expertise)
    if q:
        query = query.filter(or_(Course.title.ilike(f'%{q}%'), Course.description.ilike(f'%{q}%'), Course.sales_copy.ilike(f'%{q}%')))
    courses = query.order_by(Course.is_featured.desc(), Course.created_at.desc()).all()
    levels = [row[0] for row in db.query(Course.level).filter(Course.is_published.is_(True), Course.level.isnot(None)).distinct().all()]
    return template(request, 'catalog.html', db, {'courses': courses, 'levels': levels, 'selected_level': level, 'selected_expertise': expertise, 'q': q, 'categories': categories(db)})


@app.get('/api/expertise-areas')
def api_expertise_areas():
    return {'areas': EXPERTISE_AREAS}


@app.get('/api/certification-pathway')
def api_certification_pathway():
    return {
        'level_hours': CERTIFICATE_LEVEL_HOURS,
        'total_hours': CERTIFICATE_LEVEL_HOURS * 3,
        'levels': CERTIFICATE_LEVELS,
        'master_certificate_level': MASTER_CERTIFICATE_LEVEL,
    }


@app.get('/api/avatar-options')
def api_avatar_options():
    return {
        'text_enabled': True,
        'audio_enabled': bool(cfg.OPENAI_API_KEY),
        'video_enabled': bool(cfg.DID_API_KEY and cfg.DID_SOURCE_IMAGE_URL),
        'did_source_image_url': cfg.DID_SOURCE_IMAGE_URL if cfg.DID_SOURCE_IMAGE_URL else '',
        'openai_voice': cfg.OPENAI_VOICE,
    }


@app.get('/courses/{identifier}', response_class=HTMLResponse)
def course_detail(identifier: str, request: Request, db: Session = Depends(get_db)):
    course = find_course(db, identifier)
    if not course:
        raise HTTPException(status_code=404)
    student = student_from_request(request, db)
    enrolled = bool(student and db.query(Enrollment).filter_by(student_id=student.id, course_id=course.id, is_active=True).first())
    lessons = db.query(Lesson).filter_by(course_id=course.id).order_by(Lesson.lesson_number).all()
    return template(request, 'course_detail.html', db, {'course': course, 'lessons': lessons, 'enrolled': enrolled})


@app.get('/register', response_class=HTMLResponse)
def register_page(request: Request, next: str = '/courses'):
    with next_db_session() as db:
        return template(request, 'register.html', db, {'next_page': next})


@app.post('/register')
def register(request: Request, full_name: str = Form(...), email: str = Form(...), password: str = Form(...),
             confirm_password: str = Form(...), next: str = Form('/courses'), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if len(password) < 10 or password != confirm_password:
        return RedirectResponse(f'/register?next={next}', status_code=303)
    if db.query(Student).filter_by(email=email).first():
        return RedirectResponse(f'/login?next={next}', status_code=303)
    student = Student(username=username_from_email(db, email), full_name=full_name.strip(), email=email, password_hash=hash_password(password), is_active=True)
    db.add(student)
    db.commit()
    db.refresh(student)
    request.session['student_id'] = student.id
    return RedirectResponse(next, status_code=303)


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next: str = '/learn/dashboard'):
    with next_db_session() as db:
        return template(request, 'login.html', db, {'next_page': next})


@app.post('/login')
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form('/learn/dashboard'), db: Session = Depends(get_db)):
    student = db.query(Student).filter((Student.username == username.strip()) | (Student.email == username.strip().lower())).first()
    if not student or not verify_password(password, student.password_hash):
        return RedirectResponse(f'/login?next={next}', status_code=303)
    request.session['student_id'] = student.id
    student.last_login = datetime.utcnow()
    db.commit()
    return RedirectResponse(next, status_code=303)


@app.get('/forgot-password', response_class=HTMLResponse)
def forgot_password_page(request: Request):
    with next_db_session() as db:
        return template(request, 'forgot_password.html', db, {})


@app.post('/forgot-password')
def forgot_password(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    student = db.query(Student).filter_by(email=email.strip().lower(), is_active=True).first()
    if student and mail_configured():
        token = create_password_reset(db, student)
        db.commit()
        reset_url = f"{cfg.PUBLIC_BASE_URL.rstrip('/')}/reset-password/{token}"
        try:
            send_email(
                student.email,
                'Reset your Hub Academy password',
                (
                    f"Hello {student.full_name},\n\n"
                    "Use the link below to reset your Hub Academy password. "
                    f"This link expires in {PASSWORD_RESET_TOKEN_MINUTES} minutes.\n\n"
                    f"{reset_url}\n\n"
                    "If you did not request this, you can ignore this email."
                ),
            )
        except Exception as exc:
            logger.exception('Password reset email failed: %s', exc)
    elif student:
        logger.error('Password reset requested but mail is not configured.')
    return template(request, 'forgot_password_sent.html', db, {})


@app.get('/reset-password/{token}', response_class=HTMLResponse)
def reset_password_page(token: str, request: Request):
    with next_db_session() as db:
        reset = db.query(PasswordResetToken).filter_by(token_hash=token_hash(token), used_at=None).first()
        valid = bool(reset and reset.expires_at > datetime.utcnow())
        return template(request, 'reset_password.html', db, {'token': token if valid else '', 'valid': valid})


@app.post('/reset-password/{token}')
def reset_password(token: str, request: Request, password: str = Form(...), confirm_password: str = Form(...), db: Session = Depends(get_db)):
    reset = db.query(PasswordResetToken).filter_by(token_hash=token_hash(token), used_at=None).first()
    if not reset or reset.expires_at <= datetime.utcnow():
        return template(request, 'reset_password.html', db, {'token': '', 'valid': False})
    if len(password) < 10 or password != confirm_password:
        return template(request, 'reset_password.html', db, {'token': token, 'valid': True, 'error': 'Passwords must match and be at least 10 characters.'})

    student = db.get(Student, reset.student_id)
    if not student or not student.is_active:
        return template(request, 'reset_password.html', db, {'token': '', 'valid': False})
    student.password_hash = hash_password(password)
    reset.used_at = datetime.utcnow()
    db.commit()
    return RedirectResponse('/login', status_code=303)


@app.get('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/', status_code=303)


@app.post('/courses/{identifier}/checkout')
def checkout(identifier: str, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    if not student:
        return RedirectResponse(f'/login?next=/courses/{identifier}', status_code=303)
    course = find_course(db, identifier)
    if not course:
        raise HTTPException(status_code=404)
    amount = course.price_cents or 0
    currency = (course.currency or cfg.DEFAULT_CURRENCY).lower()
    if amount <= 0 or course.allow_free_enrollment:
        db.add(Purchase(student_id=student.id, course_id=course.id, amount_cents=0, currency=currency.upper(), status='paid', provider='free', completed_at=datetime.utcnow()))
        enroll_student(db, student.id, course.id)
        db.commit()
        return RedirectResponse(f'/learn/course/{course.id}', status_code=303)
    if not cfg.STRIPE_SECRET_KEY:
        return RedirectResponse(f'/courses/{course_slug(course)}', status_code=303)
    import stripe
    stripe.api_key = cfg.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode='payment',
        customer_email=student.email,
        line_items=[{'price_data': {'currency': currency, 'product_data': {'name': course.title}, 'unit_amount': amount}, 'quantity': 1}],
        metadata={'student_id': student.id, 'course_id': course.id},
        success_url=f"{cfg.PUBLIC_BASE_URL.rstrip()}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{cfg.PUBLIC_BASE_URL.rstrip()}/courses/{course_slug(course)}",
    )
    db.add(Purchase(student_id=student.id, course_id=course.id, amount_cents=amount, currency=currency.upper(), status='pending', provider='stripe', provider_session_id=session.id))
    db.commit()
    return RedirectResponse(session.url, status_code=303)


@app.post('/subscribe/{plan}')
def subscribe(plan: str, request: Request):
    with next_db_session() as db:
        student = student_from_request(request, db)
    if not student:
        return RedirectResponse('/login?next=/#pricing', status_code=303)
    selected = {p['name'].lower(): p for p in subscription_plans()}.get(plan.lower())
    if not selected or not cfg.STRIPE_SECRET_KEY or not selected['stripe_price_id']:
        return RedirectResponse('/#pricing', status_code=303)
    import stripe
    stripe.api_key = cfg.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode='subscription',
        customer_email=student.email,
        line_items=[{'price': selected['stripe_price_id'], 'quantity': 1}],
        metadata={'student_id': student.id, 'plan': selected['name']},
        success_url=f"{cfg.PUBLIC_BASE_URL.rstrip()}/learn/dashboard",
        cancel_url=f"{cfg.PUBLIC_BASE_URL.rstrip()}/#pricing",
    )
    return RedirectResponse(session.url, status_code=303)


@app.get('/checkout/success')
def checkout_success(session_id: str = '', request: Request = None, db: Session = Depends(get_db)):
    purchase = db.query(Purchase).filter_by(provider_session_id=session_id).first()
    if purchase and purchase.status == 'paid':
        return RedirectResponse(f'/learn/course/{purchase.course_id}', status_code=303)
    return RedirectResponse('/learn/dashboard', status_code=303)


@app.post('/stripe/webhook')
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not cfg.STRIPE_WEBHOOK_SECRET or not cfg.STRIPE_SECRET_KEY:
        return JSONResponse({'error': 'Stripe webhook is not configured'}, status_code=400)
    import stripe
    stripe.api_key = cfg.STRIPE_SECRET_KEY
    payload = await request.body()
    signature = request.headers.get('Stripe-Signature', '')
    try:
        event = stripe.Webhook.construct_event(payload, signature, cfg.STRIPE_WEBHOOK_SECRET)
    except Exception:
        return JSONResponse({'error': 'Invalid webhook'}, status_code=400)
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        purchase = db.query(Purchase).filter_by(provider_session_id=session['id']).first()
        if purchase:
            purchase.status = 'paid'
            purchase.provider_payment_intent = session.get('payment_intent')
            purchase.completed_at = datetime.utcnow()
            enroll_student(db, purchase.student_id, purchase.course_id)
            db.commit()
    return {'received': True}


@app.post('/assistant')
async def assistant(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    question = (data.get('question') or '').strip()
    mode = (data.get('mode') or 'text').strip().lower()
    courses = db.query(Course).filter_by(is_published=True).order_by(Course.is_featured.desc(), Course.created_at.desc()).limit(8).all()
    course_lines = [f"{c.title} ({c.expertise_area or c.level or 'self-paced'}, {certificate_badge(c)}, {course_price(c)})" for c in courses]
    fallback = 'I can help you choose an expertise area, pick Level 1, Level 2, or Level 3 courses, and explain the Master Certificate path.'
    if not cfg.OPENAI_API_KEY:
        if course_lines:
            fallback += ' Current courses include: ' + '; '.join(course_lines[:4]) + '.'
        return {'answer': fallback, 'mode': mode, 'audio_available': False, 'video_available': False}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            messages=[
                {'role': 'system', 'content': 'You are Hub Academy concierge. Help visitors choose online courses or subscription plans. Be concise and practical.'},
                {'role': 'user', 'content': f"Visitor question: {question}\nCertification model: each expertise area has Level 1, Level 2, and Level 3 certificates. Each level requires 15 hours across videos, practices, simulations, and evaluation. Completing all three levels grants a Master Certificate.\nAvailable courses:\n" + "\n".join(course_lines)},
            ],
            max_tokens=220,
            temperature=0.5,
        )
        return {
            'answer': response.choices[0].message.content,
            'mode': mode,
            'audio_available': bool(cfg.OPENAI_API_KEY),
            'video_available': bool(cfg.DID_API_KEY and cfg.DID_SOURCE_IMAGE_URL),
        }
    except Exception:
        return {'answer': fallback, 'mode': mode, 'audio_available': False, 'video_available': False}


@app.get('/learn/dashboard')
def learner_dashboard(request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    if not student:
        return RedirectResponse('/login?next=/learn/dashboard', status_code=303)
    enrollments = db.query(Enrollment).filter_by(student_id=student.id, is_active=True).order_by(Enrollment.enrolled_at.desc()).all()
    certificates = db.query(CertificateAward).filter_by(student_id=student.id).order_by(CertificateAward.issued_at.desc()).all()
    cards = []
    for enrollment in enrollments:
        total = db.query(Lesson).filter_by(course_id=enrollment.course_id).count()
        done = db.query(LessonProgress).join(Lesson, LessonProgress.lesson_id == Lesson.id).filter(
            LessonProgress.student_id == student.id,
            Lesson.course_id == enrollment.course_id,
            LessonProgress.is_completed.is_(True)
        ).count()
        cards.append({
            'enrollment': enrollment,
            'total': total,
            'done': done,
            'pct': int(done / total * 100) if total else 0,
            'quiz': course_quiz_stats(db, student.id, enrollment.course_id),
        })
    return template(request, 'learn/dashboard.html', db, {'student': student, 'cards': cards, 'certificates': certificates})


@app.get('/learn/course/{course_id}')
def learner_course(course_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    if not student:
        return RedirectResponse(f'/login?next=/learn/course/{course_id}', status_code=303)
    enrollment = db.query(Enrollment).filter_by(student_id=student.id, course_id=course_id, is_active=True).first()
    if not enrollment:
        return RedirectResponse('/learn/dashboard', status_code=303)
    course = db.get(Course, course_id)
    journey = journey_for_course(db, course, student.id)
    lessons = [session['lesson'] for module in journey['modules'] for session in module['sessions']]
    return template(request, 'learn/course.html', db, {'student': student, 'course': course, 'lessons': lessons, 'journey': journey, 'progress': journey['progress']})


@app.get('/learn/lesson/{lesson_id}')
def learner_lesson(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    if not student:
        return RedirectResponse(f'/login?next=/learn/lesson/{lesson_id}', status_code=303)
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404)
    enrollment = db.query(Enrollment).filter_by(student_id=student.id, course_id=lesson.course_id, is_active=True).first()
    if not enrollment:
        return RedirectResponse('/learn/dashboard', status_code=303)
    progress = db.query(LessonProgress).filter_by(student_id=student.id, lesson_id=lesson.id).first()
    if not progress:
        progress = LessonProgress(student_id=student.id, lesson_id=lesson.id, started_at=datetime.utcnow())
        db.add(progress)
    progress.last_accessed_at = datetime.utcnow()
    db.commit()
    materials = db.query(LessonMaterial).filter_by(lesson_id=lesson.id).order_by(LessonMaterial.upload_order).all()
    lessons = db.query(Lesson).filter_by(course_id=lesson.course_id).order_by(Lesson.lesson_number).all()
    grouped_materials = group_lesson_materials(materials)
    current_module_number = next((index for index, item in enumerate(lessons, start=1) if item.id == lesson.id), lesson_session_number(lesson))
    module_groups = module_material_groups(materials, current_module_number)
    module_sessions = module_session_groups(materials, current_module_number, session_objective_map(db, lesson.course_id))
    module_extras = module_extra_materials(materials)
    module_nav = module_nav_for_lessons(db, student.id, lessons, lesson.id)
    quiz_attempts = db.query(QuizAttempt).join(Quiz, QuizAttempt.quiz_id == Quiz.id).filter(
        QuizAttempt.student_id == student.id,
        Quiz.lesson_id == lesson.id,
    ).order_by(QuizAttempt.attempted_at.desc()).all()
    previous_lesson = next_lesson = None
    for index, item in enumerate(lessons):
        if item.id == lesson.id:
            previous_lesson = lessons[index - 1] if index > 0 else None
            next_lesson = lessons[index + 1] if index + 1 < len(lessons) else None
            break
    previous_progress = db.query(LessonProgress).filter_by(student_id=student.id, lesson_id=previous_lesson.id).first() if previous_lesson else None
    if previous_lesson and not session_unlocked(previous_progress):
        return RedirectResponse(f'/learn/course/{lesson.course_id}', status_code=303)
    steps = step_state(progress)
    return template(request, 'learn/lesson.html', db, {
        'student': student,
        'lesson': lesson,
        'materials': materials,
        'grouped_materials': grouped_materials,
        'module_groups': module_groups,
        'module_sessions': module_sessions,
        'module_extras': module_extras,
        'module_nav': module_nav,
        'current_module_number': current_module_number,
        'progress': progress,
        'steps': steps,
        'quiz_attempts': quiz_attempts,
        'quiz_attempts_remaining': max(0, MAX_LESSON_QUIZ_ATTEMPTS - len(quiz_attempts)),
        'quiz_pass_score': QUIZ_PASS_SCORE,
        'previous_lesson': previous_lesson,
        'next_lesson': next_lesson,
    })


@app.post('/learn/lesson/{lesson_id}/ai/flashcards')
def learner_flashcards(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not student or not lesson or not db.query(Enrollment).filter_by(student_id=student.id, course_id=lesson.course_id, is_active=True).first():
        return JSONResponse({'error': 'Not allowed'}, status_code=403)
    materials = db.query(LessonMaterial).filter_by(lesson_id=lesson.id).order_by(LessonMaterial.upload_order).all()
    prompt = f"""Create 8 concise flashcards for this lesson. Make them useful for recall and application.

{lesson_context_text(lesson, materials)}

Return only JSON:
[{{"question":"...", "answer":"..."}}]"""
    try:
        response = openai_chat_completion(
            openai_client(db, lesson.course),
            [{'role': 'user', 'content': prompt}],
            max_tokens=1200,
            temperature=0.7,
        )
        cards = parse_json_response(response.choices[0].message.content, [])
        if not isinstance(cards, list) or not cards:
            return JSONResponse({'error': 'OpenAI returned no flashcards. Try again.'}, status_code=502)
        return {'ok': True, 'flashcards': cards[:8] if isinstance(cards, list) else []}
    except Exception as exc:
        return ai_error_response(exc, 'Could not generate flashcards.')


@app.post('/learn/lesson/{lesson_id}/ai/audio-summary')
def learner_audio_summary(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not student or not lesson or not db.query(Enrollment).filter_by(student_id=student.id, course_id=lesson.course_id, is_active=True).first():
        return JSONResponse({'error': 'Not allowed'}, status_code=403)
    materials = db.query(LessonMaterial).filter_by(lesson_id=lesson.id).order_by(LessonMaterial.upload_order).all()
    prompt = f"""Write a professional spoken audio summary for this lesson. Keep it under 350 words, practical, and specific.

{lesson_context_text(lesson, materials)}"""
    try:
        client = openai_client(db, lesson.course)
        script_response = openai_chat_completion(
            client,
            [{'role': 'user', 'content': prompt}],
            max_tokens=800,
            temperature=0.6,
        )
        script = (script_response.choices[0].message.content or '').strip()
        if not script:
            return JSONResponse({'error': 'OpenAI returned no audio script. Try again.'}, status_code=502)
        audio_models = [cfg.OPENAI_AUDIO_MODEL]
        if cfg.OPENAI_AUDIO_MODEL != 'tts-1':
            audio_models.append('tts-1')
        speech = None
        last_audio_error = None
        for audio_model in audio_models:
            try:
                speech = client.audio.speech.create(
                    model=audio_model,
                    voice=cfg.OPENAI_VOICE,
                    input=script,
                )
                break
            except Exception as audio_exc:
                last_audio_error = audio_exc
        if speech is None:
            raise last_audio_error or RuntimeError('Audio generation failed.')
        return Response(content=speech.content, media_type='audio/mpeg')
    except Exception as exc:
        return ai_error_response(exc, 'Could not generate audio summary.')


@app.post('/learn/lesson/{lesson_id}/quiz/generate')
def learner_generate_quiz(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not student or not lesson or not db.query(Enrollment).filter_by(student_id=student.id, course_id=lesson.course_id, is_active=True).first():
        return JSONResponse({'error': 'Not allowed'}, status_code=403)
    attempt_count = db.query(QuizAttempt).join(Quiz, QuizAttempt.quiz_id == Quiz.id).filter(
        QuizAttempt.student_id == student.id,
        Quiz.lesson_id == lesson.id,
    ).count()
    if attempt_count >= MAX_LESSON_QUIZ_ATTEMPTS:
        return JSONResponse({'error': 'Maximum 3 quiz attempts reached for this lesson.'}, status_code=400)
    materials = db.query(LessonMaterial).filter_by(lesson_id=lesson.id).order_by(LessonMaterial.upload_order).all()
    previous_questions = []
    previous_quizzes = db.query(Quiz).filter_by(lesson_id=lesson.id, is_ai_generated=True).order_by(Quiz.created_at.desc()).limit(10).all()
    for quiz in previous_quizzes:
        for question in parse_json_response(quiz.questions_json, []):
            if isinstance(question, dict):
                previous_questions.append(question.get('question', ''))
    try:
        questions = ai_generate_quiz_questions(db, lesson, materials, previous_questions)
        if not questions:
            return JSONResponse({'error': 'Could not generate quiz questions.'}, status_code=502)
        quiz = Quiz(
            lesson_id=lesson.id,
            title=f'AI Quiz - {lesson.title}',
            questions_json=json.dumps(questions, ensure_ascii=False),
            is_ai_generated=True,
            language='en',
        )
        db.add(quiz)
        db.commit()
        db.refresh(quiz)
        public_questions = [{**q, 'correct_answer': None, 'explanation': None} for q in questions]
        return {'ok': True, 'quiz_id': quiz.id, 'questions': public_questions, 'attempts_remaining': MAX_LESSON_QUIZ_ATTEMPTS - attempt_count}
    except Exception as exc:
        return ai_error_response(exc, 'Could not generate quiz.')


@app.post('/learn/quiz/{quiz_id}/submit')
async def learner_submit_quiz(quiz_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    quiz = db.get(Quiz, quiz_id)
    if not student or not quiz or not quiz.lesson:
        return JSONResponse({'error': 'Not allowed'}, status_code=403)
    if not db.query(Enrollment).filter_by(student_id=student.id, course_id=quiz.lesson.course_id, is_active=True).first():
        return JSONResponse({'error': 'Not allowed'}, status_code=403)
    data = await request.json()
    answers = data.get('answers') or {}
    keep_quiz = bool(data.get('keep_quiz', True))
    questions = parse_json_response(quiz.questions_json, [])
    total = len(questions) or 1
    correct = 0
    for index, question in enumerate(questions):
        expected = int(question.get('correct_answer', -1))
        try:
            actual = int(answers.get(str(index), answers.get(index, -999)))
        except Exception:
            actual = -999
        if actual == expected:
            correct += 1
    score = int(round(correct / total * 100))
    try:
        ai_feedback = ai_generate_feedback(db, quiz.lesson, score, questions, answers)
    except Exception as exc:
        logger.exception('Quiz feedback failed: %s', exc)
        ai_feedback = {'feedback': 'Quiz completed. Review the explanations for missed questions.', 'recommendations': 'Review the lesson and try another quiz attempt if available.'}
    attempt = QuizAttempt(
        student_id=student.id,
        quiz_id=quiz.id,
        score=score,
        answers_json=json.dumps(answers, ensure_ascii=False),
        questions_snapshot_json=json.dumps(questions, ensure_ascii=False),
        feedback=ai_feedback['feedback'],
        recommendations=ai_feedback['recommendations'],
    )
    db.add(attempt)
    progress = db.query(LessonProgress).filter_by(student_id=student.id, lesson_id=quiz.lesson_id).first()
    if not progress:
        progress = LessonProgress(student_id=student.id, lesson_id=quiz.lesson_id, started_at=datetime.utcnow())
        db.add(progress)
    progress.quiz_completed = score >= QUIZ_PASS_SCORE
    if score >= QUIZ_PASS_SCORE:
        progress.content_viewed = True
        progress.revise_viewed = True
        progress.is_completed = True
        progress.completed_at = progress.completed_at or datetime.utcnow()
        evaluate_certificates(db, student.id, quiz.lesson.course)
    if not keep_quiz:
        quiz.title = f'Discarded quiz - {quiz.lesson.title}'
    db.commit()
    return {
        'ok': True,
        'score': score,
        'passed': score >= QUIZ_PASS_SCORE,
        'feedback': ai_feedback['feedback'],
        'recommendations': ai_feedback['recommendations'],
        'questions': questions,
    }


@app.post('/learn/lesson/{lesson_id}/step/{step_number}/complete')
def learner_complete_step(lesson_id: int, step_number: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    if not student:
        return JSONResponse({'error': 'Login required'}, status_code=401)
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404)
    enrollment = db.query(Enrollment).filter_by(student_id=student.id, course_id=lesson.course_id, is_active=True).first()
    if not enrollment:
        return JSONResponse({'error': 'Enrollment required'}, status_code=403)
    progress = db.query(LessonProgress).filter_by(student_id=student.id, lesson_id=lesson.id).first()
    if not progress:
        progress = LessonProgress(student_id=student.id, lesson_id=lesson.id, started_at=datetime.utcnow())
        db.add(progress)
    if step_number == 1:
        progress.content_viewed = True
    elif step_number == 2:
        if not progress.content_viewed:
            return JSONResponse({'error': 'Complete step 1 first'}, status_code=400)
        progress.revise_viewed = True
    elif step_number == 3:
        return JSONResponse({'error': 'Complete the AI quiz with at least 60% to finish this module.'}, status_code=400)
    else:
        return JSONResponse({'error': 'Invalid step'}, status_code=400)
    if progress.content_viewed and progress.revise_viewed and progress.quiz_completed:
        progress.is_completed = True
        progress.completed_at = progress.completed_at or datetime.utcnow()
        course = db.get(Course, lesson.course_id)
        if course:
            evaluate_certificates(db, student.id, course)
    progress.last_accessed_at = datetime.utcnow()
    db.commit()
    return {
        'ok': True,
        'is_completed': bool(progress.is_completed),
        'steps': step_state(progress),
    }


@app.post('/learn/lesson/{lesson_id}/complete')
def learner_complete_lesson(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    student = student_from_request(request, db)
    if not student:
        return RedirectResponse(f'/login?next=/learn/lesson/{lesson_id}', status_code=303)
    progress = db.query(LessonProgress).filter_by(student_id=student.id, lesson_id=lesson_id).first()
    if not progress:
        progress = LessonProgress(student_id=student.id, lesson_id=lesson_id)
        db.add(progress)
    progress.content_viewed = True
    progress.revise_viewed = True
    progress.quiz_completed = True
    progress.is_completed = True
    progress.completed_at = datetime.utcnow()
    lesson = db.get(Lesson, lesson_id)
    if lesson:
        course = db.get(Course, lesson.course_id)
        if course:
            evaluate_certificates(db, student.id, course)
    db.commit()
    return RedirectResponse(f'/learn/course/{lesson.course_id}', status_code=303)


@app.get('/admin')
@app.get('/admin/dashboard')
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    stats = {
        'programs': db.query(Program).count(),
        'courses': db.query(Course).count(),
        'students': db.query(Student).count(),
        'enrollments': db.query(Enrollment).count(),
    }
    recent_courses = db.query(Course).order_by(Course.created_at.desc()).limit(6).all()
    return template(request, 'admin/dashboard.html', db, {'admin': admin, 'stats': stats, 'recent_courses': recent_courses})


@app.get('/admin/login', response_class=HTMLResponse)
def admin_login_page(request: Request):
    with next_db_session() as db:
        return template(request, 'admin/login.html', db, {})


@app.post('/admin/login')
def admin_login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin = db.query(Admin).filter_by(username=username.strip()).first()
    if not admin or not verify_password(password, admin.password_hash):
        return RedirectResponse('/admin/login', status_code=303)
    request.session.clear()
    request.session['admin_id'] = admin.id
    return RedirectResponse('/admin/dashboard', status_code=303)


@app.get('/admin/logout')
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse('/admin/login', status_code=303)


@app.get('/admin/programs')
def admin_programs(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    programs = db.query(Program).order_by(Program.name).all()
    return template(request, 'admin/programs.html', db, {'admin': admin, 'programs': programs})


@app.post('/admin/programs')
def admin_create_program(request: Request, name: str = Form(...), description: str = Form(''), db: Session = Depends(get_db)):
    require_admin(request, db)
    db.add(Program(name=name.strip(), description=description.strip() or None))
    db.commit()
    return RedirectResponse('/admin/programs', status_code=303)


@app.post('/admin/programs/{program_id}/delete')
def admin_delete_program(program_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    program = db.get(Program, program_id)
    if program:
        db.delete(program)
        db.commit()
    return RedirectResponse('/admin/programs', status_code=303)


@app.get('/admin/courses')
def admin_courses(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    courses = db.query(Course).order_by(Course.created_at.desc()).all()
    programs = db.query(Program).order_by(Program.name).all()
    return template(request, 'admin/courses.html', db, {'admin': admin, 'courses': courses, 'programs': programs})


@app.get('/admin/courses/new')
def admin_new_course(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    programs = db.query(Program).order_by(Program.name).all()
    return template(request, 'admin/course_form.html', db, {'admin': admin, 'course': None, 'programs': programs})


@app.get('/admin/courses/{course_id}/edit')
def admin_edit_course(course_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404)
    programs = db.query(Program).order_by(Program.name).all()
    return template(request, 'admin/course_form.html', db, {'admin': admin, 'course': course, 'programs': programs})


def price_cents(value):
    try:
        return max(0, int(round(float(value or 0) * 100)))
    except (TypeError, ValueError):
        return 0


@app.post('/admin/courses')
def admin_save_course(request: Request, course_id: int = Form(0), program_id: int = Form(...), title: str = Form(...),
                      description: str = Form(''), level: str = Form(''), slug: str = Form(''),
                      sales_copy: str = Form(''), thumbnail_url: str = Form(''), price: str = Form('0'),
                      currency: str = Form('USD'), num_lessons: int = Form(0), is_published: str = Form(''),
                      is_featured: str = Form(''), allow_free_enrollment: str = Form(''),
                      expertise_area: str = Form(''), certificate_level: int = Form(0), learning_hours: int = Form(0),
                      db: Session = Depends(get_db)):
    require_admin(request, db)
    course = db.get(Course, course_id) if course_id else Course(created_at=datetime.utcnow())
    course.program_id = program_id
    course.title = title.strip()
    course.description = description.strip() or None
    course.level = level.strip() or None
    course.slug = slug.strip() or slugify(course.title)
    course.sales_copy = sales_copy.strip() or None
    course.thumbnail_url = thumbnail_url.strip() or None
    course.price_cents = price_cents(price)
    course.currency = currency.strip().upper()[:3] or 'USD'
    course.expertise_area = expertise_area.strip() or None
    course.certificate_level = certificate_level if certificate_level in (0, 1, 2, 3) else 0
    if course.certificate_level and not learning_hours:
        learning_hours = CERTIFICATE_LEVEL_HOURS
    if course.certificate_level:
        num_lessons = MODULES_PER_LEVEL
    elif not num_lessons:
        num_lessons = MODULES_PER_LEVEL
    course.num_lessons = num_lessons
    course.learning_hours = max(0, learning_hours or 0)
    course.is_published = bool(is_published)
    course.is_featured = bool(is_featured)
    course.allow_free_enrollment = bool(allow_free_enrollment)
    if not course_id:
        db.add(course)
        db.flush()
    blocked_extras = normalize_course_modules(db, course, num_lessons)
    if blocked_extras:
        db.rollback()
        return JSONResponse({
            'error': 'Extra lesson rows contain content and could not be removed automatically.',
            'extras': blocked_extras,
        }, status_code=409)
    db.commit()
    return RedirectResponse('/admin/courses', status_code=303)


@app.post('/admin/courses/{course_id}/delete')
def admin_delete_course(course_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    course = db.get(Course, course_id)
    if course:
        db.delete(course)
        db.commit()
    return RedirectResponse('/admin/courses', status_code=303)


@app.post('/admin/courses/{course_id}/bulk-prepare')
async def admin_prepare_course_bulk_import(course_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404)
    data = await request.json()
    module_names = [str(name).strip() for name in data.get('module_names', []) if str(name).strip()]
    if not module_names:
        return JSONResponse({'error': 'No module folders found.'}, status_code=400)
    module_names = module_names[:MODULES_PER_LEVEL]
    existing_lessons = db.query(Lesson).filter_by(course_id=course.id).order_by(Lesson.lesson_number).all()
    lesson_map = {}
    for index, module_name in enumerate(module_names, start=1):
        lesson = existing_lessons[index - 1] if index <= len(existing_lessons) else None
        if not lesson:
            lesson = Lesson(course_id=course.id, lesson_number=index, created_at=datetime.utcnow())
            db.add(lesson)
        lesson.lesson_number = index
        lesson.module_number = index
        lesson.session_number = 1
        lesson.duration_minutes = SESSION_DURATION_MINUTES
        lesson.title = re.sub(r'^\d+\s*[-_]\s*', '', module_name).replace('_', ' ').strip() or f'Module {index}'
        if not lesson.description:
            lesson.description = f'{lesson.title} module with five guided sessions, applied practice, and final simulation.'
        lesson_map[module_name] = lesson
    blocked_extras = []
    for extra in existing_lessons[len(module_names):]:
        has_content = (
            db.query(LessonMaterial).filter_by(lesson_id=extra.id).count()
            or db.query(LessonProgress).filter_by(lesson_id=extra.id).count()
            or db.query(Quiz).filter_by(lesson_id=extra.id).count()
        )
        if has_content:
            blocked_extras.append(extra.title)
        else:
            db.delete(extra)
    if blocked_extras:
        db.rollback()
        return JSONResponse({
            'error': 'This course has extra lesson rows with existing content. Remove them before bulk import.',
            'extras': blocked_extras,
        }, status_code=409)
    course.num_lessons = len(module_names)
    if not course.learning_hours:
        course.learning_hours = CERTIFICATE_LEVEL_HOURS
    db.commit()
    for lesson in lesson_map.values():
        db.refresh(lesson)
    return {
        'ok': True,
        'modules': [{'folder': folder, 'lesson_id': lesson.id, 'title': lesson.title} for folder, lesson in lesson_map.items()],
    }


@app.get('/admin/courses/{course_id}/lessons')
def admin_lessons(course_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404)
    blocked_extras = normalize_course_modules(db, course, MODULES_PER_LEVEL)
    if not blocked_extras:
        db.commit()
    lessons = db.query(Lesson).filter_by(course_id=course_id).order_by(Lesson.lesson_number).all()
    return template(request, 'admin/lessons.html', db, {
        'admin': admin,
        'course': course,
        'lessons': lessons[:MODULES_PER_LEVEL],
        'blocked_extras': blocked_extras,
    })


@app.post('/admin/lessons/{lesson_id}')
def admin_update_lesson(lesson_id: int, request: Request, title: str = Form(...), description: str = Form(''), db: Session = Depends(get_db)):
    require_admin(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404)
    lesson.title = title.strip()
    lesson.description = description.strip() or None
    db.commit()
    return RedirectResponse(f'/admin/courses/{lesson.course_id}/lessons', status_code=303)


@app.get('/admin/lessons/{lesson_id}/materials')
def admin_materials(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    lesson = db.get(Lesson, lesson_id)
    materials = db.query(LessonMaterial).filter_by(lesson_id=lesson_id).order_by(LessonMaterial.upload_order).all()
    current_module_number = current_module_number_for_lesson(db, lesson) if lesson else 1
    objective_rows = db.query(SessionObjective).filter_by(course_id=lesson.course_id, module_number=current_module_number).all() if lesson else []
    objective_by_session = {row.session_number: row for row in objective_rows}
    grouped = {key: [] for key, _label in MATERIAL_TYPE_OPTIONS}
    for material in materials:
        grouped.setdefault(material.material_type or 'other', []).append(material)
    return template(request, 'admin/materials.html', db, {
        'admin': admin,
        'lesson': lesson,
        'materials': materials,
        'grouped_materials': grouped,
        'material_type_options': MATERIAL_TYPE_OPTIONS,
        'r2_ready': r2_enabled(),
        'current_module_number': current_module_number,
        'objective_by_session': objective_by_session,
        'sessions_per_module': SESSIONS_PER_MODULE,
    })


@app.post('/admin/lessons/{lesson_id}/objectives')
async def admin_save_session_objectives(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404)
    current_module_number = current_module_number_for_lesson(db, lesson)
    form = await request.form()
    for session_number in range(1, SESSIONS_PER_MODULE + 1):
        title = (form.get(f'title_{session_number}') or '').strip()
        objective = (form.get(f'objective_{session_number}') or '').strip()
        existing = db.query(SessionObjective).filter_by(
            course_id=lesson.course_id,
            module_number=current_module_number,
            session_number=session_number,
        ).first()
        if not title and not objective:
            if existing:
                db.delete(existing)
            continue
        if not existing:
            existing = SessionObjective(
                course_id=lesson.course_id,
                module_number=current_module_number,
                session_number=session_number,
            )
            db.add(existing)
        existing.title = title or None
        existing.objective = objective or None
        existing.source = 'admin'
    db.commit()
    return RedirectResponse(f'/admin/lessons/{lesson.id}/materials', status_code=303)


@app.post('/admin/lessons/{lesson_id}/objectives/extract')
def admin_extract_session_objectives(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404)
    syllabus = db.query(LessonMaterial).filter_by(lesson_id=lesson.id, material_type='syllabus').order_by(LessonMaterial.upload_order.desc()).first()
    if not syllabus or not (syllabus.object_key or syllabus.file_path):
        return JSONResponse({'error': 'No syllabus PDF has been uploaded for this module.'}, status_code=400)
    try:
        text = extract_pdf_text(object_bytes(syllabus.object_key or syllabus.file_path))
        objectives = extract_topic_objectives(text)
    except Exception as exc:
        logger.exception('Syllabus objective extraction failed: %s', exc)
        return JSONResponse({'error': 'Could not extract objectives from the syllabus PDF.'}, status_code=500)
    if len(objectives) < SESSIONS_PER_MODULE:
        return JSONResponse({'error': f'Only {len(objectives)} objectives found in Topics covered.'}, status_code=422)
    module_number = current_module_number_for_lesson(db, lesson)
    saved = save_session_objectives(db, lesson.course_id, module_number, objectives)
    return {'ok': True, 'saved': saved, 'objectives': objectives[:SESSIONS_PER_MODULE]}


@app.post('/admin/lessons/{lesson_id}/materials/link')
async def admin_add_material_link(lesson_id: int, request: Request, material_type: str = Form('video'),
                                  video_url: str = Form(...), video_name: str = Form(''), db: Session = Depends(get_db)):
    require_admin(request, db)
    max_order = db.query(func.max(LessonMaterial.upload_order)).filter_by(lesson_id=lesson_id).scalar() or 0
    material = LessonMaterial(
        lesson_id=lesson_id,
        material_type=material_type,
        upload_order=max_order + 1,
        video_url=video_url.strip(),
        video_name=video_name.strip() or 'External resource',
        storage_provider='external',
    )
    db.add(material)
    db.commit()
    return RedirectResponse(f'/admin/lessons/{lesson_id}/materials', status_code=303)


@app.post('/admin/lessons/{lesson_id}/materials/presign')
async def admin_presign_material_upload(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    if not r2_enabled():
        return JSONResponse({'error': 'Cloudflare R2 is not configured.'}, status_code=400)
    data = await request.json()
    filename = (data.get('filename') or '').strip()
    content_type = (data.get('content_type') or guess_content_type(filename)).strip()
    if not filename:
        return JSONResponse({'error': 'filename is required'}, status_code=400)
    material_type = (data.get('material_type') or 'other').strip()
    key = object_key(filename, lesson_id=lesson_id, material_type=material_type)
    return {
        'upload_url': presigned_upload_url(key, content_type),
        'object_key': key,
        'content_type': content_type,
        'expires_in': cfg.R2_PRESIGN_EXPIRES_SECONDS,
    }


@app.post('/admin/lessons/{lesson_id}/materials/upload')
async def admin_upload_material_to_r2(lesson_id: int, request: Request, material_type: str = Form('other'),
                                      relative_path: str = Form(''), package_id: str = Form(''),
                                      register_metadata: str = Form('true'),
                                      file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_admin(request, db)
    lesson = db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404)
    if not r2_enabled():
        return JSONResponse({'error': 'Cloudflare R2 is not configured.'}, status_code=400)
    filename = (file.filename or '').strip()
    if not filename:
        return JSONResponse({'error': 'filename is required'}, status_code=400)

    clean_type = (material_type or 'other').strip() or 'other'
    content_type = file.content_type or guess_content_type(filename)
    clean_relative_path = (relative_path or filename).strip() or filename
    if package_id:
        key = package_object_key(clean_relative_path, lesson_id=lesson_id, material_type=clean_type, package_id=package_id)
    else:
        key = object_key(filename, lesson_id=lesson_id, material_type=clean_type)
    size_bytes = uploaded_file_size(file.file)

    try:
        file.file.seek(0)
        upload_fileobj(key, file.file, content_type)
    except Exception as exc:
        logger.exception('R2 material upload failed: %s', exc)
        return JSONResponse({'error': 'R2 upload failed before metadata was saved. Check R2 credentials, bucket permissions, and file size.'}, status_code=502)
    finally:
        await file.close()

    material = None
    if str(register_metadata).lower() not in ('false', '0', 'no', 'off'):
        try:
            material = save_r2_material_metadata(db, lesson_id, clean_type, filename, key, content_type, size_bytes)
        except Exception as exc:
            db.rollback()
            logger.exception('R2 metadata save failed after upload: %s', exc)
            return JSONResponse({
                'error': 'File reached R2, but the database record was not saved. Use Register uploaded file to repair it.',
                'object_key': key,
                'file_name': filename,
                'material_type': clean_type,
                'content_type': content_type,
                'size_bytes': size_bytes,
            }, status_code=500)
    return {
        'ok': True,
        'material_id': material.id if material else None,
        'file_name': material.file_name if material else filename,
        'material_type': material.material_type if material else clean_type,
        'object_key': material.object_key if material else key,
        'size_bytes': material.size_bytes if material else size_bytes,
    }


def save_r2_material_metadata(db: Session, lesson_id: int, material_type: str, file_name: str, object_key_value: str,
                              content_type: str = '', size_bytes: int = 0) -> LessonMaterial:
    existing = db.query(LessonMaterial).filter_by(lesson_id=lesson_id, object_key=object_key_value).first()
    if existing:
        existing.material_type = material_type
        existing.file_name = file_name or existing.file_name
        existing.file_path = object_key_value
        existing.content_type = content_type or existing.content_type
        existing.size_bytes = size_bytes or existing.size_bytes
        existing.storage_provider = 'r2'
        db.commit()
        db.refresh(existing)
        return existing
    max_order = db.query(func.max(LessonMaterial.upload_order)).filter_by(lesson_id=lesson_id).scalar() or 0
    material = LessonMaterial(
        lesson_id=lesson_id,
        material_type=material_type,
        file_name=file_name,
        file_path=object_key_value,
        object_key=object_key_value,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_provider='r2',
        upload_order=max_order + 1,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@app.post('/admin/lessons/{lesson_id}/materials/register')
async def admin_register_uploaded_material(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    if not db.get(Lesson, lesson_id):
        raise HTTPException(status_code=404)
    data = await request.json()
    object_key_value = (data.get('object_key') or '').strip()
    if not object_key_value:
        return JSONResponse({'error': 'object_key is required'}, status_code=400)
    file_name = (data.get('file_name') or posixpath.basename(object_key_value)).strip()
    material_type = (data.get('material_type') or 'other').strip() or 'other'
    content_type = (data.get('content_type') or guess_content_type(file_name)).strip()
    size_bytes = int(data.get('size_bytes') or 0)
    material = save_r2_material_metadata(db, lesson_id, material_type, file_name, object_key_value, content_type, size_bytes)
    return {'ok': True, 'material_id': material.id}


@app.post('/admin/lessons/{lesson_id}/materials/sync-r2')
def admin_sync_lesson_r2_materials(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    if not db.get(Lesson, lesson_id):
        raise HTTPException(status_code=404)
    if not r2_enabled():
        return JSONResponse({'error': 'Cloudflare R2 is not configured.'}, status_code=400)
    existing_keys = {
        row[0] for row in db.query(LessonMaterial.object_key)
        .filter(LessonMaterial.lesson_id == lesson_id, LessonMaterial.object_key.isnot(None))
        .all()
    }
    created = 0
    prefix = f'lessons/{lesson_id}/'
    try:
        for item in list_objects(prefix):
            key = item['key']
            if not key or key.endswith('/') or key in existing_keys:
                continue
            file_name = posixpath.basename(key)
            material_type = infer_material_type_from_key(key)
            save_r2_material_metadata(
                db,
                lesson_id,
                material_type,
                file_name,
                key,
                guess_content_type(file_name),
                item.get('size') or 0,
            )
            existing_keys.add(key)
            created += 1
    except Exception as exc:
        logger.exception('R2 material sync failed: %s', exc)
        return JSONResponse({'error': 'Could not list lesson files in R2. Check R2 credentials and bucket permissions.'}, status_code=502)
    return RedirectResponse(f'/admin/lessons/{lesson_id}/materials?synced={created}', status_code=303)


@app.post('/admin/lessons/{lesson_id}/materials/complete')
async def admin_complete_material_upload(lesson_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    max_order = db.query(func.max(LessonMaterial.upload_order)).filter_by(lesson_id=lesson_id).scalar() or 0
    material = LessonMaterial(
        lesson_id=lesson_id,
        material_type=(data.get('material_type') or 'article').strip(),
        file_name=(data.get('file_name') or '').strip(),
        file_path=(data.get('object_key') or '').strip(),
        object_key=(data.get('object_key') or '').strip(),
        content_type=(data.get('content_type') or '').strip(),
        size_bytes=int(data.get('size_bytes') or 0),
        storage_provider='r2',
        upload_order=max_order + 1,
    )
    if not material.object_key:
        return JSONResponse({'error': 'object_key is required'}, status_code=400)
    db.add(material)
    db.commit()
    return {'ok': True, 'material_id': material.id}


@app.post('/admin/materials/{material_id}/delete')
def admin_delete_material(material_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    material = db.get(LessonMaterial, material_id)
    lesson_id = material.lesson_id if material else 0
    if material:
        db.delete(material)
        db.commit()
    return RedirectResponse(f'/admin/lessons/{lesson_id}/materials', status_code=303)


@app.get('/materials/{material_id}/download')
def material_download(material_id: int, request: Request, db: Session = Depends(get_db)):
    material = db.get(LessonMaterial, material_id)
    if not material:
        raise HTTPException(status_code=404)
    if not material_access_allowed(material, request, db):
        return RedirectResponse(f'/login?next=/materials/{material_id}/download', status_code=303)
    if material.storage_provider == 'external' and material.video_url:
        return RedirectResponse(material.video_url, status_code=303)
    key = material.object_key or material.file_path
    if not key:
        raise HTTPException(status_code=404)
    if html_material(material):
        try:
            html = object_bytes(key).decode('utf-8')
        except UnicodeDecodeError:
            html = object_bytes(key).decode('utf-8', errors='replace')
        except Exception as exc:
            logger.exception('R2 HTML material load failed: %s', exc)
            raise HTTPException(status_code=502)
        return HTMLResponse(html)
    return RedirectResponse(presigned_download_url(key, material.file_name), status_code=303)


@app.get('/materials/{material_id}/{asset_path:path}')
def material_asset(material_id: int, asset_path: str, request: Request, db: Session = Depends(get_db)):
    material = db.get(LessonMaterial, material_id)
    if not material:
        raise HTTPException(status_code=404)
    if not material_access_allowed(material, request, db):
        return RedirectResponse(f'/login?next=/materials/{material_id}/download', status_code=303)
    if not html_material(material):
        raise HTTPException(status_code=404)
    base_key = posixpath.dirname(material.object_key or material.file_path or '')
    normalized_asset = posixpath.normpath(asset_path.replace('\\', '/')).lstrip('/')
    if normalized_asset.startswith('../') or normalized_asset == '..':
        raise HTTPException(status_code=404)
    key = posixpath.join(base_key, normalized_asset)
    filename = posixpath.basename(normalized_asset)
    return RedirectResponse(presigned_download_url(key, filename), status_code=303)


@app.get('/admin/companies')
def admin_companies(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    companies = db.query(Company).order_by(Company.name).all()
    return template(request, 'admin/companies.html', db, {'admin': admin, 'companies': companies})


@app.post('/admin/companies')
def admin_save_company(request: Request, company_id: int = Form(0), name: str = Form(...), member_id: str = Form(''),
                       email: str = Form(''), is_active: str = Form('on'), db: Session = Depends(get_db)):
    require_admin(request, db)
    company = db.get(Company, company_id) if company_id else Company(created_at=datetime.utcnow())
    company.name = name.strip()
    company.member_id = member_id.strip() or None
    company.email = email.strip() or None
    company.is_active = bool(is_active)
    if not company_id:
        db.add(company)
    db.commit()
    return RedirectResponse('/admin/companies', status_code=303)


@app.get('/admin/students')
def admin_students(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    students = db.query(Student).order_by(Student.created_at.desc()).all()
    companies = db.query(Company).order_by(Company.name).all()
    return template(request, 'admin/students.html', db, {'admin': admin, 'students': students, 'companies': companies})


@app.post('/admin/students')
def admin_save_student(request: Request, student_id: int = Form(0), full_name: str = Form(...), email: str = Form(...),
                       username: str = Form(''), password: str = Form(''), company_id: int = Form(0),
                       is_active: str = Form('on'), db: Session = Depends(get_db)):
    require_admin(request, db)
    student = db.get(Student, student_id) if student_id else Student(created_at=datetime.utcnow())
    student.full_name = full_name.strip()
    student.email = email.strip().lower()
    student.username = username.strip() or username_from_email(db, student.email)
    student.company_id = company_id or None
    student.is_active = bool(is_active)
    if password:
        student.password_hash = hash_password(password)
    elif not student_id:
        student.password_hash = hash_password('ChangeMe123!')
    if not student_id:
        db.add(student)
    db.commit()
    return RedirectResponse('/admin/students', status_code=303)


@app.get('/admin/enrollments')
def admin_enrollments(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    enrollments = db.query(Enrollment).order_by(Enrollment.enrolled_at.desc()).all()
    students = db.query(Student).filter_by(is_active=True).order_by(Student.full_name).all()
    courses = db.query(Course).order_by(Course.title).all()
    return template(request, 'admin/enrollments.html', db, {'admin': admin, 'enrollments': enrollments, 'students': students, 'courses': courses})


@app.post('/admin/enrollments')
def admin_create_enrollment(request: Request, student_id: int = Form(...), course_id: int = Form(...), db: Session = Depends(get_db)):
    require_admin(request, db)
    enroll_student(db, student_id, course_id)
    db.commit()
    return RedirectResponse('/admin/enrollments', status_code=303)


@app.post('/admin/enrollments/{enrollment_id}/delete')
def admin_delete_enrollment(enrollment_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    enrollment = db.get(Enrollment, enrollment_id)
    if enrollment:
        db.delete(enrollment)
        db.commit()
    return RedirectResponse('/admin/enrollments', status_code=303)


@app.get('/admin/progression')
def admin_progression(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    rows = []
    for enrollment in db.query(Enrollment).all():
        total = db.query(Lesson).filter_by(course_id=enrollment.course_id).count()
        done = db.query(LessonProgress).join(Lesson, LessonProgress.lesson_id == Lesson.id).filter(
            LessonProgress.student_id == enrollment.student_id,
            Lesson.course_id == enrollment.course_id,
            LessonProgress.is_completed.is_(True)
        ).count()
        rows.append({'enrollment': enrollment, 'done': done, 'total': total, 'pct': int(done / total * 100) if total else 0})
    return template(request, 'admin/progression.html', db, {'admin': admin, 'rows': rows})


@app.get('/admin/settings')
def admin_settings(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    settings = {s.key: s.value for s in db.query(Settings).all()}
    return template(request, 'admin/settings.html', db, {'admin': admin, 'settings': settings})


@app.post('/admin/settings')
def admin_save_settings(request: Request, openai_api_key: str = Form(''), db: Session = Depends(get_db)):
    require_admin(request, db)
    setting = db.query(Settings).filter_by(key='openai_api_key').first()
    if not setting:
        setting = Settings(key='openai_api_key')
        db.add(setting)
    setting.value = openai_api_key.strip()
    db.commit()
    return RedirectResponse('/admin/settings', status_code=303)
