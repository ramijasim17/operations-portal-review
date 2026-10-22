"""The site visit report as a PDF: ABC logo, the visit details, every observation (with its photo), and the approval line."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INDIGO = colors.HexColor("#43358A")
GREY = colors.HexColor("#6B6880")
LINE = colors.HexColor("#CFCDDB")


def _logo():
    try:
        from django.contrib.staticfiles import finders

        path = finders.find("core/brand/neos-lockup.png")
        if path:
            return Image(path, width=6 * cm, height=6 * cm * 51 / 260)
    except Exception:
        pass
    return None


def _photo(item, width=7 * cm):
    if not item.file:
        return None
    try:
        from PIL import Image as PILImage

        item.file.open("rb")
        try:
            img = PILImage.open(item.file)
            img = img.convert("RGB")
            img.thumbnail((900, 900))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
        finally:
            item.file.close()
        buf.seek(0)
        w, h = img.size
        return Image(buf, width=width, height=width * h / w)
    except Exception:
        return None


def build_visit_pdf(visit):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.5 * cm, bottomMargin=1.6 * cm,
                            title=f"{visit.reference} site visit report", author="ABC Operations Portal")
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["Normal"], fontSize=10, leading=13.5)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=GREY)
    h1 = ParagraphStyle("h1", parent=base["Title"], fontSize=18, alignment=0, textColor=INDIGO, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=11.5, textColor=INDIGO, spaceBefore=12, spaceAfter=5)

    def para(text, style=body):
        return Paragraph((text or "—").replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), style)

    story = []
    logo = _logo()
    head = Table([[logo or "", Paragraph(f"<b>{visit.reference}</b><br/><font size=9 color='#6B6880'>{visit.state}</font>", ParagraphStyle("r", parent=body, alignment=2))]],
                 colWidths=[10 * cm, 7.4 * cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, INDIGO), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [head, Spacer(1, 10), Paragraph("Site visit report", h1)]

    details = [
        ["Date", f"{visit.date:%d %B %Y}", "Site", visit.site],
        ["Department", visit.department.name if visit.department_id else "—", "", ""],
        ["Client", visit.client or "—", "Visited by", visit.visited_by or "—"],
        ["Purpose", visit.purpose or "—", "Others present", visit.attendees or "—"],
    ]
    t = Table([[para(a, small), para(b), para(c, small), para(d)] for a, b, c, d in details], colWidths=[2.3 * cm, 6.3 * cm, 2.6 * cm, 6.2 * cm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [t]

    story += [Paragraph("Summary", h2), para(visit.summary)]
    items = list(visit.items.all())
    if items:
        story.append(Paragraph("Observations and findings", h2))
        for n, it in enumerate(items, start=1):
            label = {"good": "Good practice", "observation": "Observation", "finding": f"Finding · {it.severity}"}[it.kind]
            colour = {"good": "#2E7D32", "finding": "#C62828"}.get(it.kind, "#43358A")
            lines = [[Paragraph(f"<b>{n}. {it.area or 'General'}</b> &nbsp; <font color='{colour}'>{label}</font>", body)], [para(it.text)]]
            if it.action_id:
                lines.append([Paragraph(f"Action raised: {it.action.action_item[:90].replace('&', '&amp;').replace('<', '&lt;')}", small)])
            img = _photo(it)
            row = Table([[Table(lines, colWidths=[9.2 * cm if img else 17.4 * cm]), img or ""]], colWidths=[9.6 * cm if img else 17.4 * cm, 7.8 * cm if img else 0.1 * cm])
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.4, LINE), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
            story.append(KeepTogether(row))
    story += [Paragraph("Next steps", h2), para(visit.next_steps)]

    approval = None
    try:
        from approvals import services

        approval = services.get(visit, "site_visit")
    except Exception:
        pass
    sign = [["Prepared by", visit.created_by.username if visit.created_by else (visit.visited_by or "—")]]
    if approval and approval.status == "approved":
        sign.append(["Approved by", f"{approval.decided_by.username if approval.decided_by else 'system'}, {approval.decided_at:%d %b %Y}" if approval.decided_at else "approved"])
    else:
        sign.append(["Approval", visit.state])
    s = Table([[para(a, small), para(b)] for a, b in sign], colWidths=[3 * cm, 14.4 * cm])
    s.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.8, INDIGO), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story += [Spacer(1, 16), s]

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GREY)
        canvas.drawString(1.8 * cm, 0.9 * cm, f"ABC Operations Portal · {visit.reference}")
        canvas.drawRightString(A4[0] - 1.8 * cm, 0.9 * cm, f"Page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
