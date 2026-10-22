"""
Builds the SGS/PBU Final Report as a .docx directly with python-docx --
no sgs_template.docx merge. Uses build_report_context() as its only
data source (the same one report_pdf.py uses), so both outputs stay in
sync and every formatting fix only ever needs to happen in
report_context.py.

Why this replaces the old docxtpl+template approach: that approach
merged live content into a pre-existing Word template whose Table of
Contents field referenced bookmarks that only existed at template-
design time. Headings added afterward via python-docx had no matching
bookmarks, hence "Error! Bookmark not defined". Building the whole
document from scratch means every heading gets Word's real "Heading 1"
style from the start, so a standard TOC field resolves correctly the
next time Word (or LibreOffice) opens the file and recomputes fields
(which we force via the updateFields setting below).
"""
import io
import re

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .report_context import build_report_context
from .report_pdf import COMPANY_COVER_STATIC_NAME, COMPANY_LOGO_STATIC_NAME, _find_static

NAVY = RGBColor(0x1F, 0x38, 0x64)
LIGHT_BLUE = "DCE6F1"
RED = RGBColor(0xC0, 0x00, 0x00)
BLACK = RGBColor(0x00, 0x00, 0x00)

PORTRAIT_W, PORTRAIT_H = Cm(21.0), Cm(29.7)   # A4
LANDSCAPE_W, LANDSCAPE_H = Cm(29.7), Cm(21.0)


def _set_cell_shading(cell, hex_color):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _set_cell_text(cell, text, color=None, bold=False, italic=False, size=9, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    if align is not None:
        p.alignment = align
    # Zero out paragraph spacing so rows don't grow taller than the
    # text itself needs -- Word's default "Normal" style spacing was
    # making every header row look padded/untidy.
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    run = p.add_run(str(text))
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _set_cell_text_with_number_highlight(cell, text, size=9):
    """Same as _set_cell_text, but any number inside the text gets its
    own red run -- matching the same red-number highlighting already
    shown on the web page's Sequence of Events table, which this export
    didn't know about since it's a completely separate rendering path."""
    cell.text = ""
    p = cell.paragraphs[0]
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0

    text = str(text or "")
    pos = 0
    for m in _NUMBER_RE.finditer(text):
        if m.start() > pos:
            run = p.add_run(text[pos:m.start()])
            run.font.size = Pt(size)
        run = p.add_run(m.group(0))
        run.font.size = Pt(size)
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
        pos = m.end()
    if pos < len(text):
        run = p.add_run(text[pos:])
        run.font.size = Pt(size)


def _set_row_height(row, height_cm):
    """Forces a compact, exact row height instead of Word's default
    auto-sizing (which pads rows well beyond what the text needs)."""
    row.height = Cm(height_cm)
    row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY


def _add_heading(doc, text):
    p = doc.add_heading(text, level=1)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in p.runs:
        run.font.color.rgb = NAVY
    return p


def _add_toc_field(paragraph):
    """Inserts a real Word TOC field (updates via F9 or automatically
    on open, since we set updateFields=True on the whole document)."""
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-1" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "Right-click and choose Update Field to build the table of contents."
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r = run._r
    r.append(fld_begin)
    r.append(instr)
    r.append(fld_sep)
    r.append(placeholder)
    r.append(fld_end)


def _set_section_orientation(section, orientation):
    if orientation == "Landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = LANDSCAPE_W, LANDSCAPE_H
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = PORTRAIT_W, PORTRAIT_H
    section.left_margin = section.right_margin = Cm(1.6)
    section.top_margin = Cm(1.4)
    section.bottom_margin = Cm(1.6)
    section.header_distance = Cm(0.5)


def _force_fixed_layout(table):
    """python-docx's table.autofit=False alone is NOT enough -- Word's
    default table layout algorithm is "autofit", which ignores explicit
    column widths entirely and instead stretches cells to fill the
    available width evenly. This forces the actual OOXML tblLayout to
    "fixed" so the widths we set are actually honored."""
    tblPr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)


def _set_table_width_dxa(table, width_emu):
    """Table.width has no working property in this python-docx version
    -- must set w:tblW directly. 1 dxa (twentieth of a point) = 635 EMU."""
    tblPr = table._tbl.tblPr
    for existing in tblPr.findall(qn("w:tblW")):
        tblPr.remove(existing)
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(width_emu) // 635))
    tblPr.insert(0, tblW)


def _set_cell_borders(cell, tags=("top", "bottom", "left", "right")):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for tag in tags:
        edge = OxmlElement(f"w:{tag}")
        edge.set(qn("w:val"), "single")
        edge.set(qn("w:sz"), "4")
        edge.set(qn("w:color"), "999999")
        borders.append(edge)
    tcPr.append(borders)


def _add_floating_picture(paragraph, image_source, width, height, offset_x, offset_y,
                            relative_from_h="page", relative_from_v="paragraph"):
    """Adds an image as a FLOATING picture ('in front of text' wrap),
    anchored at an explicit offset, instead of an inline image bound
    into the normal flow. Inline images inside a table are constrained
    by the flowing layout's width/height accounting -- two 14.5cm
    images side by side are just wide enough to exceed the landscape
    content width, which was causing Word to push the whole block to
    the next page rather than shrink it (unlike ReportLab, which
    silently scales table content to fit). A floating, anchored image
    is removed from that accounting entirely, so it renders exactly
    where placed without affecting pagination."""
    run = paragraph.add_run()
    run.add_picture(image_source, width=width, height=height)
    drawing = run._r.find(qn("w:drawing"))
    inline = drawing.find(qn("wp:inline"))

    anchor = OxmlElement("wp:anchor")
    for attr, val in [
        ("distT", "0"), ("distB", "0"), ("distL", "0"), ("distR", "0"),
        ("simplePos", "0"), ("relativeHeight", "251659264"),
        ("behindDoc", "0"), ("locked", "0"), ("layoutInCell", "1"), ("allowOverlap", "1"),
    ]:
        anchor.set(attr, val)

    simple_pos = OxmlElement("wp:simplePos")
    simple_pos.set("x", "0")
    simple_pos.set("y", "0")
    anchor.append(simple_pos)

    position_h = OxmlElement("wp:positionH")
    position_h.set("relativeFrom", relative_from_h)
    pos_offset_h = OxmlElement("wp:posOffset")
    pos_offset_h.text = str(int(offset_x))
    position_h.append(pos_offset_h)
    anchor.append(position_h)

    position_v = OxmlElement("wp:positionV")
    position_v.set("relativeFrom", relative_from_v)
    pos_offset_v = OxmlElement("wp:posOffset")
    pos_offset_v.text = str(int(offset_y))
    position_v.append(pos_offset_v)
    anchor.append(position_v)

    for tag in ("wp:extent", "wp:effectExtent"):
        el = inline.find(qn(tag))
        if el is not None:
            anchor.append(el)

    anchor.append(OxmlElement("wp:wrapNone"))

    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        el = inline.find(qn(tag))
        if el is not None:
            anchor.append(el)

    drawing.remove(inline)
    drawing.append(anchor)


def _build_header(section, header_ctx):
    """One shared header (linked across all later sections by default),
    sized to the portrait content width so it looks consistent whether
    the current page happens to be portrait or landscape.

    Built as a SINGLE flat 4-row x 4-column table (logo columns merged
    vertically across all 4 rows) rather than an outer table containing
    a nested inner table -- LibreOffice does not reliably honor fixed
    column widths on a table nested inside another table's cell, which
    was causing large unwanted gaps between the logo and the info grid
    even though the underlying OOXML widths were technically correct.
    """
    header = section.header
    header.is_linked_to_previous = False
    header.paragraphs[0].text = ""

    content_w = PORTRAIT_W - Cm(1.6) - Cm(1.6)
    left_logo_w, right_logo_w = Cm(3.4), Cm(2.2)
    mid_w = content_w - left_logo_w - right_logo_w
    col_w = mid_w // 2

    table = header.add_table(rows=4, cols=4, width=content_w)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_width_dxa(table, content_w)
    _force_fixed_layout(table)
    for row in table.rows:
        row.cells[0].width = left_logo_w
        row.cells[1].width = col_w
        row.cells[2].width = col_w
        row.cells[3].width = right_logo_w

    # Left: company logo, merged vertically across all 4 rows
    logo_cell = table.cell(0, 0)
    for r in range(1, 4):
        logo_cell = logo_cell.merge(table.cell(r, 0))
    logo_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    logo_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    logo_path = _find_static(COMPANY_LOGO_STATIC_NAME)
    if logo_path:
        try:
            logo_cell.paragraphs[0].add_run().add_picture(logo_path, width=left_logo_w - Cm(0.3))
        except Exception:
            pass

    # Right: client logo (per-report upload), merged vertically too
    right_cell = table.cell(0, 3)
    for r in range(1, 4):
        right_cell = right_cell.merge(table.cell(r, 3))
    right_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    right_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if header_ctx.get("_client_logo_storage_name"):
        try:
            from django.core.files.storage import default_storage
            with default_storage.open(header_ctx["_client_logo_storage_name"], "rb") as f:
                data = f.read()
            right_cell.paragraphs[0].add_run().add_picture(io.BytesIO(data), width=right_logo_w - Cm(0.3))
        except Exception:
            pass

    # Middle: the Field/Rig/Date/SOR/Well/Type info grid (columns 1-2)
    _set_cell_text(table.cell(0, 1), f"Field: {header_ctx['field'] or ''}", size=8,
                    align=WD_ALIGN_PARAGRAPH.CENTER)
    _set_cell_text(table.cell(0, 2), f"Rig: {header_ctx['rig'] or ''}", size=8,
                    align=WD_ALIGN_PARAGRAPH.CENTER)
    _set_cell_text(table.cell(1, 1), f"Date: {header_ctx['date_of_test'] or ''}", size=8,
                    align=WD_ALIGN_PARAGRAPH.CENTER)
    _set_cell_text(table.cell(1, 2), f"SOR: {header_ctx['sor_number'] or ''}", size=8,
                    align=WD_ALIGN_PARAGRAPH.CENTER)
    well_cell = table.cell(2, 1).merge(table.cell(2, 2))
    _set_cell_text(well_cell, f"Well: {header_ctx['well_name'] or ''}", size=8, align=WD_ALIGN_PARAGRAPH.CENTER)
    type_cell = table.cell(3, 1).merge(table.cell(3, 2))
    _set_cell_text(type_cell, f"Type of survey: {header_ctx['test_type'] or ''}", size=8,
                    align=WD_ALIGN_PARAGRAPH.CENTER)
    for r in range(4):
        _set_cell_borders(table.cell(r, 1))
        table.cell(r, 1).vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        if r < 2:
            _set_cell_borders(table.cell(r, 2))
            table.cell(r, 2).vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    for row in table.rows:
        _set_row_height(row, 0.5)


def _add_image_from_storage(doc, storage_name, max_w_cm, max_h_cm):
    from django.core.files.storage import default_storage
    from PIL import Image as PILImage
    try:
        with default_storage.open(storage_name, "rb") as f:
            data = f.read()
        pil_img = PILImage.open(io.BytesIO(data))
        w, h = pil_img.size
        scale = min(max_w_cm / (w / 96 * 2.54), max_h_cm / (h / 96 * 2.54))
        scale = min(scale, 1)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(io.BytesIO(data), width=Cm((w / 96 * 2.54) * scale))
        return True
    except Exception:
        return False


def _kv_table(doc, title, rows):
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run(title)
    run.bold = True
    run.font.size = Pt(12)
    table = doc.add_table(rows=len(rows), cols=2)
    for i, r in enumerate(rows):
        _set_cell_text(table.cell(i, 0), r["label"], bold=True, size=9)
        _set_cell_text(table.cell(i, 1), r["value"], size=9)


def build_docx(report):
    ctx = build_report_context(report)
    header_ctx = dict(ctx["header"])
    if report.client_logo:
        header_ctx["_client_logo_storage_name"] = report.client_logo.name

    doc = Document()
    section = doc.sections[0]
    _set_section_orientation(section, "Portrait")
    _build_header(section, header_ctx)

    # Force Word to recompute all fields (the TOC) on next open -- see
    # module docstring for why this is what actually fixes the TOC now.
    settings_el = doc.settings.element
    update_fields_el = OxmlElement("w:updateFields")
    update_fields_el.set(qn("w:val"), "true")
    settings_el.append(update_fields_el)

    # ── Cover page ──────────────────────────────────────────────────────
    cover = ctx["cover"]
    for _ in range(4):
        doc.add_paragraph()
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title_p.add_run(cover["title"])
    r.bold = True
    r.font.size = Pt(26)
    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub_p.add_run(cover["subtitle"])
    r.bold = True
    r.font.size = Pt(16)
    r.font.color.rgb = NAVY
    meta_p = doc.add_paragraph()
    meta_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_p.add_run(f"Well: {cover['well_name']}\nField: {cover['field']}\nDate: {cover['date_of_test']}")

    cover_shown = False
    if cover.get("photo_name"):
        cover_shown = _add_image_from_storage(doc, cover["photo_name"], 14, 9)
    if not cover_shown:
        static_cover = _find_static(COMPANY_COVER_STATIC_NAME)
        if static_cover:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(static_cover, width=Cm(12))
    doc.add_page_break()

    # ── Table of Contents ────────────────────────────────────────────────
    _add_heading(doc, "Table of Contents")
    toc_p = doc.add_paragraph()
    _add_toc_field(toc_p)
    doc.add_page_break()

    # ── Survey Objectives ────────────────────────────────────────────────
    _add_heading(doc, "Survey Objectives")
    doc.add_paragraph(ctx["objectives_text"])
    doc.add_page_break()

    # ── Basic Well Information ──────────────────────────────────────────
    _add_heading(doc, "Basic Well Information")
    _kv_table(doc, "GENERAL INFORMATION", ctx["general_information"])
    _kv_table(doc, "OPERATION DETAILS", ctx["operation_details"])
    _kv_table(doc, "WELL DATA", ctx["well_data"])
    doc.add_page_break()

    # ── Sequence of Events ──────────────────────────────────────────────
    _add_heading(doc, "Sequence Of Events")
    for shift in ctx["shifts"]:
        sp = doc.add_paragraph()
        sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = sp.add_run(shift["label"])
        r.bold = True
        if shift["rows"]:
            table = doc.add_table(rows=1, cols=4)
            table.style = "Table Grid"
            hdr = table.rows[0].cells
            for i, text in enumerate(["SR", "Start", "End", "Operation Description"]):
                _set_cell_text(hdr[i], text, bold=True, size=9)
            for row in shift["rows"]:
                if row["date_changed"]:
                    banner = table.add_row().cells
                    banner[0].merge(banner[3])
                    _set_cell_text(banner[0], row["date"].strftime("%d/%m/%Y"), bold=True,
                                   align=WD_ALIGN_PARAGRAPH.CENTER, size=9)
                    _set_cell_shading(banner[0], "FFFF00")
                cells = table.add_row().cells
                _set_cell_text(cells[0], row["sr"], size=9)
                _set_cell_text(cells[1], row["start"], size=9)
                _set_cell_text(cells[2], row["end"], size=9)
                _set_cell_text_with_number_highlight(cells[3], row["description"], size=9)
        doc.add_paragraph()
    doc.add_page_break()

    # ── Landscape section starts here: Results Table, Result Plots,
    # and the two wide gauge plots ───────────────────────────────────────
    landscape_section = doc.add_section(WD_SECTION.NEW_PAGE)
    _set_section_orientation(landscape_section, "Landscape")

    _add_heading(doc, f"{report.test_type} Results Table")
    if ctx["has_stations"]:
        header_row = ["Step\nNO", "MD-mGL", "ft GL", "Average", "Start", "End", "Bar", "Psia",
                      "Temp\nPrimary", "Temp\nOther", "Pressure\npsi/ft", "Temp\ndegF/ft", "\u0394Psi/min"]
        table = doc.add_table(rows=1, cols=13)
        table.style = "Table Grid"
        for i, text in enumerate(header_row):
            _set_cell_text(table.rows[0].cells[i], text, bold=True, size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER)
        for row in ctx["station_rows"]:
            cells = table.add_row().cells
            if row["kind"] == "station":
                values = [str(row["step_no"]), row["md_mgl"], row["ft_gl"], "", row["start"], row["end"],
                          row["bar"], row["psia"], row["temp_primary"], row["temp_other"], "", "", ""]
                for i, v in enumerate(values):
                    if v:
                        _set_cell_text(cells[i], v, size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER,
                                        color=RED if str(v).startswith("(") else BLACK)
            else:
                values = ["", "", "", row["avg_mgl"], "", "", "", "", "", "",
                          row["pressure_gradient"], row["temp_gradient"], row["delta_psi_per_min"]]
                for cell in cells:
                    _set_cell_shading(cell, LIGHT_BLUE)
                for i, v in enumerate(values):
                    if v:
                        _set_cell_text(cells[i], v, size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER,
                                        italic=True, color=RED if str(v).startswith("(") else NAVY)
    else:
        doc.add_paragraph("[No station readings entered yet]")
    doc.add_page_break()

    _add_heading(doc, f"{report.test_type} Result Plots")
    if ctx["has_enough_for_plots"]:
        from .views import _build_gradient_chart_buffer, _build_tp_chart_buffer
        plot_size = Cm(14.5)
        gap = Cm(0.3)
        total_w = plot_size + gap + plot_size
        start_x = (LANDSCAPE_W - total_w) // 2  # center the pair on the full page width
        anchor_p = doc.add_paragraph()
        _add_floating_picture(anchor_p, _build_gradient_chart_buffer(report), plot_size, plot_size,
                                start_x, Cm(0.3))
        _add_floating_picture(anchor_p, _build_tp_chart_buffer(report), plot_size, plot_size,
                                start_x + plot_size + gap, Cm(0.3))
        # Reserve vertical space matching the floated images' height so
        # nothing else on this page tries to occupy the same space --
        # this page ends in a page break immediately after anyway.
        for _ in range(2):
            doc.add_paragraph()
    else:
        doc.add_paragraph("[Need at least 2 station readings to plot]")
    doc.add_page_break()

    for section_ctx in ctx["image_sections"]:
        if section_ctx["key"] not in {"overall_gauge_plot", "gauges_comparison"}:
            continue
        _add_heading(doc, section_ctx["label"])
        if section_ctx.get("image_name"):
            if not _add_image_from_storage(doc, section_ctx["image_name"], 26, 13):
                doc.add_paragraph("[Image could not be loaded]")
        else:
            doc.add_paragraph("[Image not yet uploaded]")
        doc.add_page_break()

    # ── Back to portrait for the remaining image sections ───────────────
    portrait_section2 = doc.add_section(WD_SECTION.NEW_PAGE)
    _set_section_orientation(portrait_section2, "Portrait")

    remaining = [s for s in ctx["image_sections"] if s["key"] not in {"overall_gauge_plot", "gauges_comparison"}]
    for i, section_ctx in enumerate(remaining):
        label = section_ctx["label"] + (f" {section_ctx['heading_suffix']}" if section_ctx.get("heading_suffix") else "")
        _add_heading(doc, label)
        if section_ctx.get("image_name"):
            if not _add_image_from_storage(doc, section_ctx["image_name"], 16, 20):
                doc.add_paragraph("[Image could not be loaded]")
            if section_ctx.get("caption"):
                doc.add_paragraph(section_ctx["caption"])
        else:
            doc.add_paragraph("[Image not yet uploaded]")
        if i + 1 < len(remaining):
            doc.add_page_break()

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
