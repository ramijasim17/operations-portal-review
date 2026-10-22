"""
Builds the Equipment Manifest as a PDF, matching the layout of the
sample document: company logo + title header, job ticket info, one
bordered table per classification (each starting on its own page),
and a Prepared/Checked/Approved By sign-off line at the end.
"""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

NAVY = colors.HexColor("#1F3864")
GREY_BG = colors.HexColor("#E7E7E7")
GREY_LINE = colors.HexColor("#999999")


def _find_static(name):
    """Static assets are looked up project-wide (not scoped to one app),
    so this reuses the exact same company logo already set up for the
    SGS reports at sgs_report/static/sgs_report/company_logo.png."""
    try:
        from django.contrib.staticfiles import finders
        return finders.find(name)
    except Exception:
        return None


def build_manifest_pdf(job):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("ManifestTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=20))
    styles.add(ParagraphStyle("SectionHeading", parent=styles["Normal"], fontSize=12, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("JobInfo", parent=styles["Normal"], fontSize=10, spaceAfter=4))

    buffer = io.BytesIO()

    def on_page(canvas, doc):
        canvas.saveState()
        width, height = canvas._pagesize
        margin = doc.leftMargin

        logo_path = _find_static("sgs_report/company_logo.png")
        if logo_path:
            try:
                from PIL import Image as PILImage
                pil_img = PILImage.open(logo_path)
                w, h = pil_img.size
                logo_h = 1.2 * cm
                logo_w = logo_h * (w / h)
                canvas.drawImage(logo_path, margin, height - margin - logo_h,
                                  width=logo_w, height=logo_h, mask="auto")
            except Exception:
                pass

        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(width / 2, height - margin - 0.7 * cm, "Equipment Manifest")

        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(width - margin, 1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=3 * cm, bottomMargin=1.8 * cm,
        title=f"Equipment Manifest \u2014 {job.job_name}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="Main", frames=[frame], onPage=on_page)])

    story = []

    story.append(Paragraph(f"Job Ticket: {job.job_name}", ParagraphStyle(
        "JobTicket", parent=styles["Normal"], fontSize=13, fontName="Helvetica-Bold", spaceAfter=6)))
    story.append(Paragraph(
        f"Client: {job.client or '\u2014'} &nbsp;|&nbsp; Date: {job.date or '\u2014'} "
        f"&nbsp;|&nbsp; Location: {job.location or '\u2014'}",
        styles["JobInfo"],
    ))
    story.append(Spacer(1, 0.5 * cm))

    assets_by_classification = {}
    for asset in job.assets():
        assets_by_classification.setdefault(asset.classification, []).append(asset)

    classifications = list(assets_by_classification.items())
    sign_table_data = [[
        "Prepared By: ____________________", "Checked By: ____________________", "Approved By: ____________________",
    ]]

    if not classifications:
        story.append(Paragraph("No equipment on this manifest yet.", styles["Normal"]))
    else:
        for i, (classification, assets) in enumerate(classifications):
            header_row = [f"{classification.name.upper()} ({len(assets)} items)"]
            header_table = Table([header_row], colWidths=[doc.width])
            header_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), GREY_BG),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 0.75, GREY_LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(header_table)

            field_defs = list(classification.fields.all())
            n_extra = len(field_defs)

            # A table this wide needs small, WRAPPING text or dense field
            # lists (this classification alone can have 10+) overlap the
            # neighbouring column instead of wrapping onto a second line --
            # plain strings in a reportlab Table never wrap; only Paragraph
            # cells do. Font shrinks further as columns get tighter so
            # headers like "Working pressure" still fit two lines, not one
            # overflowing line.
            if n_extra <= 4:
                cell_font_size, header_font_size = 9, 9
            elif n_extra <= 8:
                cell_font_size, header_font_size = 8, 8
            else:
                cell_font_size, header_font_size = 7, 7

            header_style = ParagraphStyle(
                "ManifestColHeader", parent=styles["Normal"], fontName="Helvetica-Bold",
                fontSize=header_font_size, leading=header_font_size + 2, alignment=TA_LEFT,
            )
            cell_style = ParagraphStyle(
                "ManifestCell", parent=styles["Normal"], fontName="Helvetica",
                fontSize=cell_font_size, leading=cell_font_size + 2, alignment=TA_LEFT,
            )

            header_cells = [Paragraph("Serial Number", header_style), Paragraph("Description", header_style)]
            header_cells += [Paragraph(fd.label, header_style) for fd in field_defs]
            data = [header_cells]

            for asset in assets:
                values = asset.values_by_field_id()
                row = [Paragraph(str(asset.name), cell_style), Paragraph(str(asset.description or "\u2014"), cell_style)]
                for fd in field_defs:
                    v = values.get(fd.id)
                    row.append(Paragraph(str(v) if v not in (None, "") else "\u2014", cell_style))
                data.append(row)

            if n_extra:
                fixed_w = doc.width * 0.40
                extra_w = (doc.width - fixed_w) / n_extra
                col_widths = [doc.width * 0.15, doc.width * 0.25] + [extra_w] * n_extra
            else:
                col_widths = [doc.width * 0.35, doc.width * 0.65]
            t = Table(data, colWidths=col_widths, repeatRows=1)
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, GREY_LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(t)

            # Sign-off line on every classification's own page/section, not
            # just once at the very end.
            story.append(Spacer(1, 1.2 * cm))
            sign_table = Table(sign_table_data, colWidths=[doc.width / 3] * 3)
            sign_table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9)]))
            story.append(sign_table)

            if i < len(classifications) - 1:
                story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return buffer