"""
Builds the SGS/PBU Final Report as a PDF directly with ReportLab -- no
HTML, no Word template merge. Uses build_report_context() as its only
data source, so formatting fixes only ever need to happen in
report_context.py.

Why ReportLab instead of WeasyPrint: it's pure Python (no system
libraries like Pango/Cairo), so it installs with a plain `pip install
reportlab` and works on Render's native Python buildpack with no
Docker/Aptfile needed.

The Table of Contents is REAL: ReportLab's TableOfContents flowable
plus a two-pass multiBuild() computes actual page numbers from the
actual layout, so there is no "Error! Bookmark not defined" -- that
class of bug does not exist in this approach.
"""
import io
import re
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from .report_context import build_report_context

NAVY = colors.HexColor("#1F3864")

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _highlight_numbers_pdf(text):
    """Wraps every number in red <font> markup for a ReportLab
    Paragraph -- matching the same red-number highlighting already
    shown on the web page's Sequence of Events table. The text is
    XML-escaped first since Paragraph parses its string as mini-XML,
    so this is also safe against any stray < > & characters."""
    escaped = _xml_escape(str(text or ""))
    return _NUMBER_RE.sub(lambda m: f'<font color="#C00000"><b>{m.group(0)}</b></font>', escaped)
LIGHT_BLUE = colors.HexColor("#DCE6F1")
RED = colors.HexColor("#C00000")
GREY = colors.HexColor("#999999")

PORTRAIT_SIZE = A4
LANDSCAPE_SIZE = landscape(A4)

# Company-branding assets that live outside the database entirely (they
# were baked into the old sgs_template.docx). Drop matching files into
# sgs_report/static/sgs_report/ with these exact names and they'll be
# picked up automatically -- no code change needed on your end.
#   - company_logo.png  -> small mark shown on the LEFT of the running
#                          header on every page (e.g. the ABC mark)
#   - company_cover.jpg -> the big photo on the cover page (e.g. the
#                          "nationalenergy Oilfield Services" truck photo)
COMPANY_LOGO_STATIC_NAME = "sgs_report/company_logo.png"
COMPANY_COVER_STATIC_NAME = "sgs_report/company_cover.jpg"


def _find_static(name):
    """Looks up a bundled static asset by its app-relative static path
    and returns an absolute filesystem path, or None if it doesn't
    exist yet -- callers must handle None gracefully (skip drawing)
    rather than erroring, since these two files are optional."""
    try:
        from django.contrib.staticfiles import finders
        return finders.find(name)
    except Exception:
        return None


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("SGSHeading", parent=ss["Heading1"], alignment=TA_CENTER,
                           textColor=NAVY, spaceAfter=6))
    ss.add(ParagraphStyle("SGSSubtitle", parent=ss["Normal"], alignment=TA_CENTER,
                           fontName="Helvetica-Bold", fontSize=11, spaceAfter=10))
    ss.add(ParagraphStyle("SGSBody", parent=ss["Normal"], fontSize=10, leading=14))
    ss.add(ParagraphStyle("SGSCoverTitle", parent=ss["Title"], alignment=TA_CENTER, fontSize=22))
    ss.add(ParagraphStyle("SGSCoverSub", parent=ss["Title"], alignment=TA_CENTER, fontSize=16,
                           textColor=NAVY))
    ss.add(ParagraphStyle("SGSCoverMeta", parent=ss["Normal"], alignment=TA_CENTER, fontSize=11,
                           leading=16))
    return ss


def _open_image(url_or_path, max_w, max_h):
    """Opens an uploaded image (via Django storage) and returns a
    ReportLab Image flowable scaled to fit inside max_w x max_h while
    preserving aspect ratio. Returns None if the file can't be read."""
    from django.core.files.storage import default_storage
    from PIL import Image as PILImage

    # url_or_path here is actually the storage *name*, resolved by the
    # caller before this function is used (see _storage_name helper).
    try:
        with default_storage.open(url_or_path, "rb") as f:
            data = f.read()
    except Exception:
        return None
    pil_img = PILImage.open(io.BytesIO(data))
    w, h = pil_img.size
    scale = min(max_w / w, max_h / h)
    return Image(io.BytesIO(data), width=w * scale, height=h * scale)


def _square_image(buf, size):
    """Places a matplotlib PNG buffer into an exact size x size box."""
    return Image(buf, width=size, height=size)


def _draw_running_header(canvas, doc, header):
    """Drawn on every page: the bordered Field/Rig/Date/SOR/Well/Type
    table, with the company mark on the left and the client's logo on
    the right, matching the sample report's page header layout."""
    canvas.saveState()
    width, height = canvas._pagesize  # actual current page size -- doc.pagesize
    margin = doc.leftMargin           # is NOT reliably updated per PageTemplate
    left_logo_w = 3.6 * cm
    right_logo_w = 2.2 * cm
    gap = 0.3 * cm  # explicit breathing room around each logo, both sides
    table_width = width - 2 * margin - left_logo_w - right_logo_w - 2 * gap
    table_x = margin + left_logo_w + gap

    data = [
        [f"Field: {header['field'] or ''}", f"Rig: {header['rig'] or ''}"],
        [f"Date: {header['date_of_test'] or ''}", f"SOR: {header['sor_number'] or ''}"],
        [f"Well: {header['well_name'] or ''}", ""],
        [f"Type of survey: {header['test_type'] or ''}", ""],
    ]
    t = Table(data, colWidths=[table_width / 2] * 2)
    t.setStyle(TableStyle([
        ("SPAN", (0, 2), (1, 2)), ("SPAN", (0, 3), (1, 3)),
        ("ALIGN", (0, 2), (-1, 3), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    tw, th = t.wrapOn(canvas, table_width, 3 * cm)
    top_inset = 0.5 * cm
    top_y = height - top_inset
    t.drawOn(canvas, table_x, top_y - th)  # table drawn first, logos drawn on top after

    def _place_logo(storage_or_static_path, box_x, box_w, from_static=False):
        try:
            safe_w = box_w * 0.92
            safe_h = th * 0.92
            if from_static:
                if not storage_or_static_path:
                    return
                from PIL import Image as PILImage
                pil_img = PILImage.open(storage_or_static_path)
                w, h = pil_img.size
                scale = min(safe_w / w, safe_h / h)
                img = Image(storage_or_static_path, width=w * scale, height=h * scale)
            else:
                img = _open_image(storage_or_static_path, safe_w, safe_h)
            if img:
                logo_x = box_x + (box_w - img.drawWidth) / 2
                logo_y = top_y - th + (th - img.drawHeight) / 2
                img.drawOn(canvas, logo_x, logo_y)
        except Exception:
            pass

    # Left: company mark (static asset -- see COMPANY_LOGO_STATIC_NAME)
    _place_logo(_find_static(COMPANY_LOGO_STATIC_NAME), margin, left_logo_w, from_static=True)
    # Right: client logo (per-report upload)
    if header.get("_client_logo_storage_name"):
        _place_logo(header["_client_logo_storage_name"], table_x + table_width + gap, right_logo_w)

    canvas.restoreState()


def build_pdf(report):
    ctx = build_report_context(report)
    header = ctx["header"]
    if report.client_logo:
        header["_client_logo_storage_name"] = report.client_logo.name

    styles = _styles()
    buffer = io.BytesIO()

    def on_page(canvas, doc):
        _draw_running_header(canvas, doc, header)

    # Portrait and landscape need different top margins: the running
    # header's own height is fixed (~2.8cm of real clearance needed)
    # regardless of orientation, but landscape pages need to reclaim
    # every possible millimeter of frame height for the 14.5cm plot
    # images -- so each orientation gets its own tightest-safe margin
    # instead of sharing one value tuned only for portrait.
    PORTRAIT_TOP = 3.6 * cm
    LANDSCAPE_TOP = 3.0 * cm

    doc = BaseDocTemplate(
        buffer, pagesize=PORTRAIT_SIZE,
        leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=PORTRAIT_TOP, bottomMargin=1.6 * cm,
        title=f"{report.well_name} Final Report",
    )
    portrait_frame = Frame(doc.leftMargin, doc.bottomMargin,
                            doc.width, doc.height, id="portrait")
    landscape_frame = Frame(doc.leftMargin, doc.bottomMargin,
                             LANDSCAPE_SIZE[0] - doc.leftMargin - doc.rightMargin,
                             LANDSCAPE_SIZE[1] - LANDSCAPE_TOP - doc.bottomMargin, id="landscape")
    doc.addPageTemplates([
        PageTemplate(id="Portrait", frames=[portrait_frame], pagesize=PORTRAIT_SIZE, onPage=on_page),
        PageTemplate(id="Landscape", frames=[landscape_frame], pagesize=LANDSCAPE_SIZE, onPage=on_page),
    ])

    story = []

    # ── Cover page ──────────────────────────────────────────────────────
    cover = ctx["cover"]
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph(cover["title"], styles["SGSCoverTitle"]))
    story.append(Paragraph(cover["subtitle"], styles["SGSCoverSub"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        f"Well: {cover['well_name']}<br/>Field: {cover['field']}<br/>Date: {cover['date_of_test']}",
        styles["SGSCoverMeta"],
    ))
    cover_photo_shown = False
    if ctx["cover"].get("photo_name"):
        try:
            from django.core.files.storage import default_storage
            from PIL import Image as PILImage
            with default_storage.open(ctx["cover"]["photo_name"], "rb") as f:
                data = f.read()
            pil_img = PILImage.open(io.BytesIO(data))
            w, h = pil_img.size
            max_w, max_h = doc.width, 10 * cm
            scale = min(max_w / w, max_h / h)
            story.append(Spacer(1, 1 * cm))
            photo = Image(io.BytesIO(data), width=w * scale, height=h * scale)
            photo.hAlign = "CENTER"
            story.append(photo)
            cover_photo_shown = True
        except Exception:
            pass
    if not cover_photo_shown:
        static_path = _find_static(COMPANY_COVER_STATIC_NAME)
        if static_path:
            try:
                from PIL import Image as PILImage
                pil_img = PILImage.open(static_path)
                w, h = pil_img.size
                max_w, max_h = doc.width, 10 * cm
                scale = min(max_w / w, max_h / h)
                story.append(Spacer(1, 1 * cm))
                photo = Image(static_path, width=w * scale, height=h * scale)
                photo.hAlign = "CENTER"
                story.append(photo)
            except Exception:
                pass
    story.append(PageBreak())

    # ── Table of Contents (real page numbers, computed by multiBuild) ──
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(name="TOCHeading1", fontSize=11, leading=14, leftIndent=0),
    ]
    story.append(Paragraph("Table of Contents", styles["SGSHeading"]))
    story.append(toc)
    story.append(PageBreak())

    def heading(text):
        p = Paragraph(text, styles["SGSHeading"])
        story.append(p)

    # ── Survey Objectives ────────────────────────────────────────────────
    heading("Survey Objectives")
    story.append(Paragraph(ctx["objectives_text"], styles["SGSBody"]))
    story.append(PageBreak())

    # ── Basic Well Information ──────────────────────────────────────────
    heading("Basic Well Information")

    def kv_table(title, rows):
        story.append(Paragraph(title, ParagraphStyle(
            "kvtitle", parent=styles["Normal"], fontName="Helvetica-Bold",
            fontSize=12, alignment=TA_CENTER, spaceBefore=8, spaceAfter=6)))
        data = [[r["label"], r["value"]] for r in rows]
        t = Table(data, colWidths=[5 * cm, 10 * cm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)

    kv_table("GENERAL INFORMATION", ctx["general_information"])
    kv_table("OPERATION DETAILS", ctx["operation_details"])
    kv_table("WELL DATA", ctx["well_data"])
    story.append(PageBreak())

    # ── Sequence of Events ──────────────────────────────────────────────
    heading("Sequence Of Events")
    for shift in ctx["shifts"]:
        story.append(Paragraph(shift["label"], ParagraphStyle(
            "shifttitle", parent=styles["Normal"], fontName="Helvetica-Bold",
            fontSize=11, alignment=TA_CENTER, spaceAfter=6)))
        if shift["rows"]:
            data = [["SR", "Start", "End", "Operation Description"]]
            row_styles = []
            for row in shift["rows"]:
                if row["date_changed"]:
                    data.append([row["date"].strftime("%d/%m/%Y"), "", "", ""])
                    banner_idx = len(data) - 1
                    row_styles.append(("SPAN", (0, banner_idx), (3, banner_idx)))
                    row_styles.append(("BACKGROUND", (0, banner_idx), (3, banner_idx), colors.yellow))
                    row_styles.append(("ALIGN", (0, banner_idx), (3, banner_idx), "CENTER"))
                data.append([row["sr"], row["start"], row["end"],
                             Paragraph(_highlight_numbers_pdf(row["description"]), styles["SGSBody"])])
            t = Table(data, colWidths=[1.5 * cm, 2.5 * cm, 2.5 * cm, 10 * cm], repeatRows=1)
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, GREY),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ] + row_styles))
            story.append(t)
        story.append(Spacer(1, 0.4 * cm))

    # ── Results Table (landscape) ───────────────────────────────────────
    story.append(NextPageTemplate("Landscape"))
    story.append(PageBreak())
    heading(f"{report.test_type} Results Table")
    if ctx["has_stations"]:
        header_row1 = ["Step\nNO", "MD-mGL", "ft GL", "Average", "Start", "End", "Bar", "Psia",
                        "Temp\nPrimary", "Temp\nOther", "Pressure\npsi/ft", "Temp\ndegF/ft", "\u0394Psi/min"]
        table_data = [header_row1]
        cell_styles = []
        for i, row in enumerate(ctx["station_rows"], start=1):
            if row["kind"] == "station":
                table_data.append([
                    str(row["step_no"]), row["md_mgl"], row["ft_gl"], "", row["start"], row["end"],
                    row["bar"], row["psia"], row["temp_primary"], row["temp_other"], "", "", "",
                ])
            else:
                table_data.append(["", "", "", row["avg_mgl"], "", "", "", "", "", "",
                                    row["pressure_gradient"], row["temp_gradient"], row["delta_psi_per_min"]])
                cell_styles.append(("BACKGROUND", (0, i), (-1, i), LIGHT_BLUE))
                cell_styles.append(("TEXTCOLOR", (0, i), (-1, i), NAVY))
                cell_styles.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Oblique"))
            # negative-value cells in red, for either row kind
            for col_idx, val in enumerate(table_data[-1]):
                if isinstance(val, str) and val.startswith("("):
                    cell_styles.append(("TEXTCOLOR", (col_idx, i), (col_idx, i), RED))
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, GREY),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ] + cell_styles))
        story.append(t)
    else:
        story.append(Paragraph("[No station readings entered yet]", styles["SGSBody"]))
    story.append(PageBreak())

    # ── Result Plots (14.5cm x 14.5cm each, side by side) ───────────────
    from reportlab.platypus import KeepTogether
    plots_heading = Paragraph(f"{report.test_type} Result Plots", styles["SGSHeading"])
    if ctx["has_enough_for_plots"]:
        from .views import _build_gradient_chart_buffer, _build_tp_chart_buffer
        plot_size = 14.5 * cm
        img1 = _square_image(_build_gradient_chart_buffer(report), plot_size)
        img2 = _square_image(_build_tp_chart_buffer(report), plot_size)
        t = Table([[img1, img2]])
        t.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(KeepTogether([plots_heading, t]))
    else:
        story.append(plots_heading)
        story.append(Paragraph("[Need at least 2 station readings to plot]", styles["SGSBody"]))

    # ── Remaining image sections (generic loop) ─────────────────────────
    # Two of these (overall_gauge_plot, gauges_comparison) are wide
    # landscape-oriented charts and render much better on a landscape
    # page; everything else (completion diagrams, photos, calibration
    # certs) stays portrait, matching the original report's layout.
    LANDSCAPE_SECTION_KEYS = {"overall_gauge_plot", "gauges_comparison"}
    PORTRAIT_MAX_W = PORTRAIT_SIZE[0] - 2 * 1.6 * cm
    LANDSCAPE_MAX_W = LANDSCAPE_SIZE[0] - 2 * 1.6 * cm

    sections = ctx["image_sections"]
    # The template switch out of Result Plots (which is Landscape) must
    # target whatever the FIRST image section actually needs -- not
    # unconditionally "Portrait". Getting this wrong was the exact bug
    # that left "Overall Downhole Memory Gauge Plot" stuck in portrait.
    first_orientation = "Portrait"
    if sections and sections[0]["key"] in LANDSCAPE_SECTION_KEYS:
        first_orientation = "Landscape"
    if first_orientation != "Landscape":  # Result Plots section is Landscape
        story.append(NextPageTemplate(first_orientation))
    story.append(PageBreak())
    current_orientation = first_orientation
    for i, section in enumerate(sections):
        orientation = "Landscape" if section["key"] in LANDSCAPE_SECTION_KEYS else "Portrait"
        max_img_w = LANDSCAPE_MAX_W if orientation == "Landscape" else PORTRAIT_MAX_W
        max_img_h = 13 * cm if orientation == "Landscape" else 20 * cm

        heading(section["label"] + (f" {section['heading_suffix']}" if section.get("heading_suffix") else ""))
        if section.get("image_name"):
            img = _open_image(section["image_name"], max_img_w, max_img_h)
            if img:
                img.hAlign = "CENTER"
                story.append(img)
                if section.get("caption"):
                    story.append(Paragraph(section["caption"], styles["SGSBody"]))
            else:
                story.append(Paragraph("[Image could not be loaded]", styles["SGSBody"]))
        else:
            story.append(Paragraph("[Image not yet uploaded]", styles["SGSBody"]))

        if i + 1 < len(sections):
            next_key = sections[i + 1]["key"]
            next_orientation = "Landscape" if next_key in LANDSCAPE_SECTION_KEYS else "Portrait"
            if next_orientation != current_orientation:
                story.append(NextPageTemplate(next_orientation))
            story.append(PageBreak())
            current_orientation = next_orientation
        # last section: no trailing PageBreak, avoids a spurious blank
        # final page

    def _after_flowable(flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "SGSHeading":
            text = flowable.getPlainText()
            doc.notify("TOCEntry", (0, text, doc.page))

    doc.afterFlowable = _after_flowable
    doc.multiBuild(story)
    buffer.seek(0)
    return buffer
