"""Certificate PDF generation + public verification.

This module is fully self-contained and additive: it defines its own APIRouter,
its own Jinja templates instance, and reuses the existing CertificateAward data
(including the verification_code already stored on every award). It does NOT
modify any existing function, route, or the certificate-award logic.

Public verification lets an employer type a certificate ID (HUB-XXXXXXXX) and
confirm authenticity. Students can download a stamped PDF of their credential.

Wiring (in fastapi_app.py): app.include_router(certificate_verify.router)
"""

import os
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import fastapi_config as cfg
from fastapi_db import CertificateAward, Student, get_db

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(cfg.BASE_DIR, 'fastapi_templates'))

MASTER_CERTIFICATE_LEVEL = 4
HUB_LOGO = os.path.join(cfg.BASE_DIR, 'static', 'images', 'hub_academy_logo.jpg')


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------
def to_hub_code(verification_code: str) -> str:
    """Public, human-typeable ID derived from the stored verification_code.

    e.g. verification_code '4f9a2c7b1d...' -> 'HUB-4F9A2C7B'. Hex is naturally
    free of ambiguous characters (no O/I/L), so it reads cleanly off print.
    """
    return 'HUB-' + (verification_code or '')[:8].upper()


def find_award(db: Session, code: str):
    """Resolve a typed/scanned code to an award. Accepts the HUB-XXXXXXXX form
    or the full raw verification_code. Returns None if not found/ambiguous."""
    s = (code or '').strip()
    if s.upper().startswith('HUB-'):
        s = s[4:]
    s = s.strip().lower()
    if not s:
        return None
    matches = db.query(CertificateAward).filter(
        CertificateAward.verification_code.like(s + '%')
    ).limit(2).all()
    if len(matches) == 1:
        return matches[0]
    return db.query(CertificateAward).filter(
        CertificateAward.verification_code == s
    ).first()


def level_label(level: int) -> str:
    if level == MASTER_CERTIFICATE_LEVEL:
        return 'Master Certificate'
    return f'Level {level}'


def base_url(request: Request) -> str:
    """Absolute base URL for verify links/QR. Prefer PUBLIC_BASE_URL when it is
    a real (non-localhost) value; otherwise fall back to the request's own host
    (which is correct automatically on Cloud Run)."""
    configured = (cfg.PUBLIC_BASE_URL or '').rstrip('/')
    if configured and '127.0.0.1' not in configured and 'localhost' not in configured:
        return configured
    return str(request.base_url).rstrip('/')


def _student(request: Request, db: Session):
    sid = request.session.get('student_id')
    if not sid:
        return None
    student = db.get(Student, int(sid))
    if not student or not student.is_active:
        return None
    return student


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _draw_seal(c, cx, cy, ring_color, caption):
    from reportlab.lib.units import mm
    r = 16 * mm
    c.saveState()
    c.setStrokeColor(ring_color)
    c.setLineWidth(2.2)
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.setLineWidth(0.7)
    c.circle(cx, cy, r - 2.6 * mm, stroke=1, fill=0)
    try:
        c.drawImage(HUB_LOGO, cx - 11 * mm, cy - 6.5 * mm, width=22 * mm, height=13 * mm,
                    preserveAspectRatio=True, mask='auto')
    except Exception:
        pass
    c.setFillColor(ring_color)
    c.setFont('Helvetica-Bold', 7)
    c.drawCentredString(cx, cy - r - 5 * mm, caption)
    c.restoreState()


def _draw_qr(c, data, x, y, size_mm):
    from reportlab.lib.units import mm
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    widget = QrCodeWidget(data)
    bounds = widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    size = size_mm * mm
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def build_certificate_pdf(award: CertificateAward, verify_url: str, hub: str) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as rl_canvas

    navy = colors.HexColor('#0f2350')
    blue = colors.HexColor('#2480DC')
    gold = colors.HexColor('#c9a227')
    slate = colors.HexColor('#5b6b86')

    buf = BytesIO()
    W, H = landscape(A4)
    c = rl_canvas.Canvas(buf, pagesize=landscape(A4))

    # background + borders
    c.setFillColor(colors.white); c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(navy); c.rect(0, H - 9 * mm, W, 9 * mm, fill=1, stroke=0)
    c.setFillColor(navy); c.rect(0, 0, W, 9 * mm, fill=1, stroke=0)
    c.setStrokeColor(gold); c.setLineWidth(3); c.rect(13 * mm, 13 * mm, W - 26 * mm, H - 26 * mm)
    c.setLineWidth(0.8); c.rect(16 * mm, 16 * mm, W - 32 * mm, H - 32 * mm)

    # logo
    try:
        c.drawImage(HUB_LOGO, W / 2 - 16 * mm, H - 50 * mm, width=32 * mm, height=24 * mm,
                    preserveAspectRatio=True, mask='auto')
    except Exception:
        pass
    c.setFillColor(slate); c.setFont('Helvetica', 8)
    c.drawCentredString(W / 2, H - 52 * mm, 'Powered by Hub Academy')

    # title
    c.setFillColor(navy); c.setFont('Helvetica-Bold', 30)
    c.drawCentredString(W / 2, H - 68 * mm, 'CERTIFICATE OF COMPLETION')
    c.setStrokeColor(gold); c.setLineWidth(2)
    c.line(W / 2 - 42 * mm, H - 72 * mm, W / 2 + 42 * mm, H - 72 * mm)

    c.setFillColor(slate); c.setFont('Helvetica', 13)
    c.drawCentredString(W / 2, H - 84 * mm, 'This is proudly presented to')

    student_name = award.student.full_name if award.student else 'Student'
    c.setFillColor(navy); c.setFont('Helvetica-Bold', 34)
    c.drawCentredString(W / 2, H - 100 * mm, student_name)
    c.setStrokeColor(colors.HexColor('#e7eef7')); c.setLineWidth(1)
    c.line(W / 2 - 72 * mm, H - 104 * mm, W / 2 + 72 * mm, H - 104 * mm)

    c.setFillColor(slate); c.setFont('Helvetica', 13)
    c.drawCentredString(W / 2, H - 116 * mm, 'for successfully completing the')
    c.setFillColor(blue); c.setFont('Helvetica-Bold', 18)
    c.drawCentredString(W / 2, H - 126 * mm, award.title)

    c.setFillColor(slate); c.setFont('Helvetica-Oblique', 10)
    detail = f"{award.expertise_area}  ·  {level_label(award.certificate_level)}  ·  {award.hours_completed} learning hours"
    c.drawCentredString(W / 2, H - 135 * mm, detail)

    # seal + signature
    seal_y = 48 * mm
    _draw_seal(c, W / 2, seal_y, gold, 'Hub Academy')
    c.setStrokeColor(navy); c.setLineWidth(1)
    c.line(W / 2 - 32 * mm, seal_y - 3 * mm, W / 2 + 32 * mm, seal_y - 3 * mm)
    c.setFillColor(slate); c.setFont('Helvetica', 9)
    c.drawCentredString(W / 2, seal_y - 8 * mm, 'Authorised by Hub Academy')

    # QR + verify caption (bottom-right)
    try:
        _draw_qr(c, verify_url, W - 52 * mm, 22 * mm, 26)
        c.setFillColor(slate); c.setFont('Helvetica', 7.5)
        c.drawCentredString(W - 39 * mm, 19 * mm, 'Scan to verify')
    except Exception:
        pass

    issued = award.issued_at or datetime.utcnow()
    c.setFillColor(slate); c.setFont('Helvetica', 8.5)
    c.drawCentredString(W / 2, 21 * mm,
                        f"Issued {issued.strftime('%d %B %Y')}   ·   Verification ID: {hub}")
    c.setFont('Helvetica', 7.5)
    c.drawCentredString(W / 2, 17 * mm, f"Verify at {verify_url}")

    c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public verification
# ---------------------------------------------------------------------------
def _render_result(request: Request, db: Session, code: str):
    award = find_award(db, code)
    ctx = {
        'request': request,
        'current_user': _student(request, db),
        'query_code': code,
    }
    if not award:
        ctx.update({'valid': False})
    else:
        issued = award.issued_at or datetime.utcnow()
        ctx.update({
            'valid': True,
            'hub': to_hub_code(award.verification_code),
            'student_name': award.student.full_name if award.student else 'Student',
            'title': award.title,
            'expertise_area': award.expertise_area,
            'level_label': level_label(award.certificate_level),
            'hours': award.hours_completed,
            'issued_at': issued.strftime('%d %B %Y'),
        })
    return templates.TemplateResponse('verify_result.html', ctx)


@router.get('/verify', response_class=HTMLResponse)
def verify_form(request: Request, code: str = '', db: Session = Depends(get_db)):
    if code.strip():
        return _render_result(request, db, code.strip())
    return templates.TemplateResponse('verify.html', {
        'request': request,
        'current_user': _student(request, db),
    })


@router.get('/verify/{code}', response_class=HTMLResponse)
def verify_code(code: str, request: Request, db: Session = Depends(get_db)):
    return _render_result(request, db, code)


# ---------------------------------------------------------------------------
# Student: list + download PDF
# ---------------------------------------------------------------------------
@router.get('/learn/certificates', response_class=HTMLResponse)
def my_certificates(request: Request, db: Session = Depends(get_db)):
    student = _student(request, db)
    if not student:
        return RedirectResponse('/login?next=/learn/certificates', status_code=303)
    awards = db.query(CertificateAward).filter_by(student_id=student.id).order_by(
        CertificateAward.issued_at.desc()).all()
    rows = [{
        'hub': to_hub_code(a.verification_code),
        'title': a.title,
        'expertise_area': a.expertise_area,
        'level_label': level_label(a.certificate_level),
        'hours': a.hours_completed,
        'issued_at': (a.issued_at or datetime.utcnow()).strftime('%d %B %Y'),
        'verify_url': f"{base_url(request)}/verify/{to_hub_code(a.verification_code)}",
    } for a in awards]
    return templates.TemplateResponse('learn/certificates.html', {
        'request': request,
        'current_user': student,
        'student': student,
        'certificates': rows,
    })


@router.get('/learn/certificate/{code}')
def download_certificate(code: str, request: Request, db: Session = Depends(get_db)):
    student = _student(request, db)
    if not student:
        return RedirectResponse(f'/login?next=/learn/certificate/{code}', status_code=303)
    award = find_award(db, code)
    if not award or award.student_id != student.id:
        # Do not leak whether the code exists; behave as not-found for this user.
        return Response(status_code=404)
    hub = to_hub_code(award.verification_code)
    verify_url = f"{base_url(request)}/verify/{hub}"
    pdf = build_certificate_pdf(award, verify_url, hub)
    safe = (student.full_name or 'certificate').replace(' ', '_')
    filename = f"Certificate_{hub}_{safe}.pdf"
    return Response(content=pdf, media_type='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{filename}"'})
