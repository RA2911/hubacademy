import re
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from fastapi_db import Admin, Student, get_db


def username_from_email(db: Session, email: str) -> str:
    base = re.sub(r'[^a-zA-Z0-9_]+', '_', email.split('@', 1)[0]).strip('_').lower() or 'learner'
    username = base
    suffix = 1
    while db.query(Student).filter_by(username=username).first():
        suffix += 1
        username = f'{base}_{suffix}'
    return username


def hash_password(password: str) -> str:
    return generate_password_hash(password, method='pbkdf2:sha256')


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def current_student(request: Request, db: Session = Depends(get_db)) -> Optional[Student]:
    student_id = request.session.get('student_id')
    if not student_id:
        return None
    student = db.get(Student, int(student_id))
    if not student or not student.is_active:
        request.session.clear()
        return None
    return student


def admin_from_request(request: Request, db: Session) -> Optional[Admin]:
    admin_id = request.session.get('admin_id')
    if not admin_id:
        return None
    return db.get(Admin, int(admin_id))
