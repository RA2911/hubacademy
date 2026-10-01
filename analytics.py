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
    if not ip:
        return 'Unknown'
    if not _geo['tried']:
        _geo['tried'] = True
        try:
            import geoip2.database
            path = getattr(cfg, 'GEOIP_DB_PATH', '')
            if path and os.path.exists(path):
                _geo['reader'] = geoip2.database.Reader(path)
        except Exception:
            _geo['reader'] = None
    reader = _geo['reader']
    if not reader:
        return 'Unknown'
    try:
        return reader.country(ip).country.name or 'Unknown'
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
