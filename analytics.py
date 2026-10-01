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


_PDF_NAVY = '#0f2350'
_PDF_BLUE = '#2480DC'
_PDF_TEAL = '#16A6AC'
_PDF_GOLD = '#c9a227'
_PDF_GREEN = '#2b8a3e'
_PDF_SLATE = '#5b6b86'
_PDF_LIGHT = '#eef3fa'
_PDF_PALETTE = ['#2480DC', '#16A6AC', '#c9a227', '#2b8a3e', '#7b5bff', '#e8590c', '#e03131', '#8a97ab']


def _trend_drawing(series, width, height):
    """Area + line trend of daily views over 30 days."""
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Line, String, Polygon, Rect
    d = Drawing(width, height)
    pad_l, pad_b, pad_t, pad_r = 26, 22, 12, 8
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_b - pad_t
    counts = [s['count'] for s in series]
    maxv = max(counts + [1])
    n = len(series)
    # gridlines + y labels
    for g in range(0, 5):
        yy = pad_b + plot_h * g / 4.0
        d.add(Line(pad_l, yy, pad_l + plot_w, yy, strokeColor=colors.HexColor('#e6ebf3'), strokeWidth=0.6))
        d.add(String(pad_l - 5, yy - 3, str(int(round(maxv * g / 4.0))),
                     fontSize=6, fillColor=colors.HexColor(_PDF_SLATE), textAnchor='end'))

    def px(i):
        return pad_l + (plot_w * i / (n - 1) if n > 1 else 0)

    def py(v):
        return pad_b + (plot_h * v / maxv if maxv else 0)

    pts = [(px(i), py(c)) for i, c in enumerate(counts)]
    # filled area
    poly = [pad_l, pad_b]
    for x, y in pts:
        poly += [x, y]
    poly += [pad_l + plot_w, pad_b]
    d.add(Polygon(poly, fillColor=colors.HexColor('#dcebfb'), strokeColor=None))
    # line
    for i in range(len(pts) - 1):
        d.add(Line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1],
                   strokeColor=colors.HexColor(_PDF_BLUE), strokeWidth=1.6))
    # x labels (every ~6 days)
    for i, s in enumerate(series):
        if i % 6 == 0 or i == n - 1:
            d.add(String(px(i), pad_b - 10, s['date'][5:], fontSize=6,
                         fillColor=colors.HexColor(_PDF_SLATE), textAnchor='middle'))
    return d


def _donut_drawing(items, title, width, height):
    """Donut chart with a side legend. Falls back to 'No data'."""
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, String
    d = Drawing(width, height)
    d.add(String(6, height - 12, title, fontSize=10, fillColor=colors.HexColor(_PDF_NAVY),
                 fontName='Helvetica-Bold'))
    total = sum(i['count'] for i in items) if items else 0
    if not items or total <= 0:
        d.add(String(width / 2.0, height / 2.0, 'No data yet', fontSize=9,
                     fillColor=colors.HexColor(_PDF_SLATE), textAnchor='middle'))
        return d
    try:
        from reportlab.graphics.charts.doughnut import Doughnut
        dn = Doughnut()
        dn.width = 86
        dn.height = 86
        dn.x = 8
        dn.y = (height - 86) / 2.0 - 4
        dn.data = [it['count'] for it in items]
        dn.innerRadiusFraction = 0.55
        for i in range(len(items)):
            dn.slices[i].fillColor = colors.HexColor(_PDF_PALETTE[i % len(_PDF_PALETTE)])
            dn.slices[i].strokeColor = colors.white
            dn.slices[i].strokeWidth = 1.2
        d.add(dn)
    except Exception:
        pass
    # legend (manual, to the right)
    from reportlab.graphics.shapes import Rect
    ly = height - 30
    for i, it in enumerate(items[:6]):
        col = colors.HexColor(_PDF_PALETTE[i % len(_PDF_PALETTE)])
        d.add(Rect(104, ly, 8, 8, fillColor=col, strokeColor=None))
        pct = round(it['count'] * 100.0 / total)
        label = str(it['label'])
        if len(label) > 16:
            label = label[:15] + '…'
        d.add(String(116, ly, f"{label}  {it['count']} ({pct}%)", fontSize=7.5,
                     fillColor=colors.HexColor('#1f2733')))
        ly -= 13
    return d


def build_report_pdf(data) -> bytes:
    """Render the weekly analytics report as a polished, designed PDF."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, KeepTogether)

    W, H = A4
    buf = BytesIO()
    content_w = W - 30 * mm
    navy = colors.HexColor(_PDF_NAVY)
    slate = colors.HexColor(_PDF_SLATE)

    def _header_footer(c, doc):
        c.saveState()
        # header band
        c.setFillColor(navy)
        c.rect(0, H - 24 * mm, W, 24 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor(_PDF_GOLD))
        c.rect(0, H - 24 * mm, W, 1.6 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 16)
        c.drawString(15 * mm, H - 14 * mm, 'Hub Academy')
        c.setFont('Helvetica', 10)
        c.setFillColor(colors.HexColor('#c7d3e8'))
        c.drawString(15 * mm, H - 19.5 * mm, 'Weekly Analytics Report')
        c.setFont('Helvetica', 8.5)
        c.drawRightString(W - 15 * mm, H - 19.5 * mm, data.get('generated', ''))
        # footer
        c.setStrokeColor(colors.HexColor('#e6ebf3'))
        c.setLineWidth(0.6)
        c.line(15 * mm, 12 * mm, W - 15 * mm, 12 * mm)
        c.setFillColor(slate)
        c.setFont('Helvetica', 7.5)
        c.drawString(15 * mm, 8 * mm, 'Private & self-hosted · hubacademy.ai')
        c.drawRightString(W - 15 * mm, 8 * mm, 'Page %d' % doc.page)
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=30 * mm, bottomMargin=16 * mm)
    h2 = ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=12, textColor=navy, spaceAfter=6, spaceBefore=4)
    num = ParagraphStyle('num', fontName='Helvetica-Bold', fontSize=21, textColor=navy, alignment=1, leading=22)
    lbl = ParagraphStyle('lbl', fontName='Helvetica', fontSize=7.5, textColor=slate, alignment=1, leading=9)
    story = []

    s = data['stats']

    def kpi(n, label):
        return [Paragraph(str(n), num), Paragraph(label, lbl)]

    kpis = [[kpi(s['views_30d'], 'VIEWS · 30 DAYS'), kpi(s['uniq_30d'], 'UNIQUE · 30 DAYS'),
             kpi(s['views_7d'], 'VIEWS · 7 DAYS'), kpi(s['views_today'], 'VIEWS · TODAY')]]
    kt = Table(kpis, colWidths=[content_w / 4.0] * 4, rowHeights=[20 * mm])
    kt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(_PDF_LIGHT)),
        ('INNERGRID', (0, 0), (-1, -1), 5, colors.white),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEABOVE', (0, 0), (0, 0), 3, colors.HexColor(_PDF_BLUE)),
        ('LINEABOVE', (1, 0), (1, 0), 3, colors.HexColor(_PDF_TEAL)),
        ('LINEABOVE', (2, 0), (2, 0), 3, colors.HexColor(_PDF_GOLD)),
        ('LINEABOVE', (3, 0), (3, 0), 3, colors.HexColor(_PDF_GREEN)),
    ]))
    story.append(kt)
    story.append(Spacer(1, 8 * mm))

    # Trend
    story.append(Paragraph('Views — last 30 days', h2))
    story.append(_trend_drawing(data['series'], content_w, 52 * mm))
    story.append(Spacer(1, 7 * mm))

    # Two donuts: devices + countries
    dev = _donut_drawing(data['devices'], 'Devices', content_w / 2.0 - 4, 48 * mm)
    ctr = _donut_drawing(data['countries'], 'Countries', content_w / 2.0 - 4, 48 * mm)
    donuts = Table([[dev, ctr]], colWidths=[content_w / 2.0] * 2)
    donuts.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(donuts)
    story.append(Spacer(1, 6 * mm))

    # Events row (as small cards)
    ev_items = [{'label': k.replace('_', ' ').title(), 'count': v} for k, v in data['event_counts'].items()]
    story.append(Paragraph('Events — last 30 days', h2))
    if ev_items:
        cells = [[Paragraph(str(it['count']), num), Paragraph(it['label'].upper(), lbl)] for it in ev_items]
        # chunk into rows of 4
        rows = [cells[i:i + 4] for i in range(0, len(cells), 4)]
        for r in rows:
            while len(r) < 4:
                r.append([Paragraph('', num)])
            et = Table([r], colWidths=[content_w / 4.0] * 4, rowHeights=[16 * mm])
            et.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f6f9ff')),
                ('INNERGRID', (0, 0), (-1, -1), 5, colors.white),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            story.append(et)
            story.append(Spacer(1, 2 * mm))
    else:
        story.append(Paragraph('<font color="#8a97ab">No events recorded yet.</font>', lbl))
    story.append(Spacer(1, 4 * mm))

    # Tables: top pages + referrers
    def data_table(title, items, label_head):
        block = [Paragraph(title, h2)]
        head = [Paragraph(f'<font color="#ffffff"><b>{label_head}</b></font>', lbl),
                Paragraph('<font color="#ffffff"><b>VIEWS</b></font>', lbl)]
        body = [head]
        for it in (items or [])[:8]:
            body.append([Paragraph(str(it['label'])[:64], lbl), Paragraph(str(it['count']), lbl)])
        if len(body) == 1:
            body.append([Paragraph('No data yet', lbl), Paragraph('', lbl)])
        tt = Table(body, colWidths=[content_w - 26 * mm, 26 * mm])
        st = [('BACKGROUND', (0, 0), (-1, 0), navy),
              ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
              ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
              ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
              ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
              ('LINEBELOW', (0, 1), (-1, -1), 0.5, colors.HexColor('#e6ebf3'))]
        for ri in range(1, len(body)):
            if ri % 2 == 0:
                st.append(('BACKGROUND', (0, ri), (-1, ri), colors.HexColor('#f6f9ff')))
        tt.setStyle(TableStyle(st))
        block.append(tt)
        return KeepTogether(block)

    story.append(data_table('Top pages', data['top_pages'], 'PAGE'))
    story.append(Spacer(1, 5 * mm))
    story.append(data_table('Top referrers', data['top_referrers'], 'SOURCE'))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
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
