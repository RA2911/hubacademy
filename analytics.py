"""Private, self-hosted analytics. Logs page views + events to our own DB.

Everything here is best-effort and fully wrapped in try/except: analytics must
NEVER raise into a request and break the site. Country uses a local MaxMind
GeoLite2 database when present (cfg.GEOIP_DB_PATH); otherwise 'Unknown'.
"""

import os
import re
from datetime import datetime, timedelta

import fastapi_config as cfg
from fastapi_db import SessionLocal, PageView, AnalyticsEvent

_BOT_RE = re.compile(r'bot|crawl|spider|slurp|bing|baidu|yandex|duckduck|headless|'
                     r'monitor|pingdom|uptime|preview|facebookexternal|whatsapp|'
                     r'telegram|python-requests|curl|wget', re.I)


def is_bot(ua: str) -> bool:
    return bool(ua and _BOT_RE.search(ua))


def device_from_ua(ua: str) -> str:
    ua = (ua or '').lower()
    if not ua:
        return 'unknown'
    if 'ipad' in ua or 'tablet' in ua or ('android' in ua and 'mobile' not in ua):
        return 'tablet'
    if 'mobi' in ua or 'iphone' in ua or 'ipod' in ua or 'android' in ua:
        return 'mobile'
    return 'desktop'


def client_ip(request) -> str:
    xff = request.headers.get('x-forwarded-for', '')
    if xff:
        return xff.split(',')[0].strip()
    try:
        return request.client.host if request.client else ''
    except Exception:
        return ''


_geo = {'reader': None, 'tried': False}


def country_from_ip(ip: str) -> str:
    """Offline country lookup via geoip2fast (data bundled in the package — no
    account, no external calls). Returns 'Unknown' on anything unexpected."""
    if not ip:
        return 'Unknown'
    if not _geo['tried']:
        _geo['tried'] = True
        try:
            from geoip2fast import GeoIP2Fast
            _geo['reader'] = GeoIP2Fast()
        except Exception:
            _geo['reader'] = None
    reader = _geo['reader']
    if not reader:
        return 'Unknown'
    try:
        name = (getattr(reader.lookup(ip), 'country_name', '') or '').strip()
        low = name.lower()
        if not name or low.startswith(('reserved', 'private', 'unknown', '<')):
            return 'Unknown'
        return name
    except Exception:
        return 'Unknown'


def log_pageview(path, visitor_id, student_id, referrer, country, device):
    db = None
    try:
        db = SessionLocal()
        db.add(PageView(
            path=(path or '')[:300], visitor_id=visitor_id, student_id=student_id,
            referrer=(referrer or '')[:300], country=country or 'Unknown', device=device or 'unknown',
        ))
        db.commit()
    except Exception:
        try:
            if db:
                db.rollback()
        except Exception:
            pass
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def log_event(name, visitor_id=None, student_id=None, detail=None):
    db = None
    try:
        db = SessionLocal()
        db.add(AnalyticsEvent(
            name=(name or '')[:40], visitor_id=visitor_id, student_id=student_id,
            detail=(detail or '')[:300] if detail else None,
        ))
        db.commit()
    except Exception:
        try:
            if db:
                db.rollback()
        except Exception:
            pass
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass


def collect_stats(db):
    """Aggregate all Analytics-page metrics. Shared by the admin page and the
    weekly PDF report."""
    from sqlalchemy import func, distinct
    from collections import Counter
    from fastapi_db import PageView as PV, AnalyticsEvent as AE
    now = datetime.utcnow()

    def _since(days):
        return now - timedelta(days=days)

    def _views(days):
        return db.query(func.count(PV.id)).filter(PV.created_at >= _since(days)).scalar() or 0

    def _uniques(days):
        return db.query(func.count(distinct(PV.visitor_id))).filter(PV.created_at >= _since(days)).scalar() or 0

    stats = {
        'views_today': _views(1), 'views_7d': _views(7), 'views_30d': _views(30),
        'uniq_today': _uniques(1), 'uniq_7d': _uniques(7), 'uniq_30d': _uniques(30),
        'total_views': db.query(func.count(PV.id)).scalar() or 0,
    }

    rows = db.query(PV.created_at).filter(PV.created_at >= _since(30)).all()
    daily = Counter()
    for (ts,) in rows:
        if ts:
            daily[ts.strftime('%Y-%m-%d')] += 1
    series = []
    for i in range(29, -1, -1):
        d = (now - timedelta(days=i)).strftime('%Y-%m-%d')
        series.append({'date': d, 'count': daily.get(d, 0)})
    maxc = max([s['count'] for s in series] + [1])
    for s in series:
        s['pct'] = int(round(s['count'] * 100 / maxc))

    def _top(col, limit=8, fallback='Unknown'):
        q = (db.query(col, func.count(PV.id))
               .filter(PV.created_at >= _since(30))
               .group_by(col).order_by(func.count(PV.id).desc()).limit(limit))
        return [{'label': (r[0] or fallback), 'count': r[1]} for r in q.all()]

    ev = (db.query(AE.name, func.count(AE.id))
            .filter(AE.created_at >= _since(30))
            .group_by(AE.name).all())

    return {
        'stats': stats,
        'series': series,
        'top_pages': _top(PV.path),
        'top_referrers': _top(PV.referrer, fallback='Direct / none'),
        'countries': _top(PV.country),
        'devices': _top(PV.device, limit=5),
        'event_counts': {name: cnt for name, cnt in ev},
        'retention_days': getattr(cfg, 'ANALYTICS_RETENTION_DAYS', 90),
        'generated': 'Generated ' + now.strftime('%Y-%m-%d %H:%M UTC'),
    }


def build_report_pdf(data) -> bytes:
    """Render the weekly analytics report as a PDF (reportlab)."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as rl_canvas

    navy = colors.HexColor('#0f2350')
    slate = colors.HexColor('#5b6b86')
    buf = BytesIO()
    W, H = A4
    c = rl_canvas.Canvas(buf, pagesize=A4)
    state = {'y': H - 20 * mm}

    def line(txt, size=10, bold=False, color=colors.black, dx=18):
        if state['y'] < 25 * mm:
            c.showPage()
            state['y'] = H - 20 * mm
        c.setFillColor(color)
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        c.drawString(dx * mm, state['y'], txt)
        state['y'] -= (size + 4) * 0.42 * mm + 3 * mm

    def row(label, count):
        if state['y'] < 25 * mm:
            c.showPage()
            state['y'] = H - 20 * mm
        c.setFillColor(colors.black)
        c.setFont('Helvetica', 10)
        c.drawString(22 * mm, state['y'], str(label)[:70])
        c.drawRightString(W - 18 * mm, state['y'], str(count))
        state['y'] -= 6 * mm

    def gap(mm_amt=4):
        state['y'] -= mm_amt * mm

    def section(title, items):
        line(title, size=12, bold=True, color=navy)
        if not items:
            row('—', '')
        else:
            for it in items:
                row(it['label'], it['count'])
        gap(4)

    line('Hub Academy — Weekly Analytics', size=20, bold=True, color=navy)
    line(data.get('generated', ''), size=10, color=slate)
    gap(4)

    s = data['stats']
    line('Summary', size=12, bold=True, color=navy)
    row('Views — today', s['views_today'])
    row('Views — last 7 days', s['views_7d'])
    row('Views — last 30 days', s['views_30d'])
    row('Views — all time', s['total_views'])
    row('Unique visitors — today', s['uniq_today'])
    row('Unique visitors — last 7 days', s['uniq_7d'])
    row('Unique visitors — last 30 days', s['uniq_30d'])
    gap(4)

    ev_items = [{'label': k.replace('_', ' ').title(), 'count': v} for k, v in data['event_counts'].items()]
    section('Events (30 days)', ev_items)
    section('Top pages (30 days)', data['top_pages'])
    section('Top referrers (30 days)', data['top_referrers'])
    section('Countries (30 days)', data['countries'])
    section('Devices (30 days)', data['devices'])

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def prune(days: int = 90):
    db = None
    try:
        cutoff = datetime.utcnow() - timedelta(days=int(days or 90))
        db = SessionLocal()
        db.query(PageView).filter(PageView.created_at < cutoff).delete(synchronize_session=False)
        db.query(AnalyticsEvent).filter(AnalyticsEvent.created_at < cutoff).delete(synchronize_session=False)
        db.commit()
    except Exception:
        try:
            if db:
                db.rollback()
        except Exception:
            pass
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass
