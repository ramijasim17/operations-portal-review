"""
Single source of truth for turning an SGSReport into the data both the
PDF (ReportLab) and Word (python-docx) generators render. Every
formatting decision (decimal places, HH:MM:SS vs HH:MM, negative-in-
parentheses, which unit is "primary" vs "other") lives HERE ONLY, so a
future formatting fix only ever needs to happen in one place.
"""
from .models import fmt_signed


def _kv(label, value):
    return {"label": label, "value": "" if value is None else str(value)}


def build_report_context(report):
    ctx = {"report": report}
    existing_images = {img.section: img for img in report.images.all()}

    # ── Header block (repeats on every page) ───────────────────────────
    ctx["header"] = {
        "field": report.field,
        "rig": report.rig,
        "date_of_test": report.date_of_test,
        "sor_number": report.sor_number,
        "well_name": report.well_name,
        "test_type": report.test_type,
        "client_logo_url": report.client_logo.url if report.client_logo else None,
    }

    # ── Cover page ──────────────────────────────────────────────────────
    cover_img = existing_images.get("cover")
    ctx["cover"] = {
        "title": "FINAL REPORT",
        "subtitle": f"{report.report_type}-{report.test_type}",
        "well_name": report.well_name,
        "field": report.field,
        "date_of_test": report.date_of_test,
        "photo_name": cover_img.image.name if cover_img else None,
    }

    # ── Survey Objectives (fixed sentence, built from live report
    # fields). "PBU" jobs combine a pressure build-up with a gradient
    # survey, so both are named; "SGS"/"FGS" reports ARE the survey
    # itself, so naming the report_type again would just repeat the
    # test_type ("SGS & Static Gradient Survey" reads as redundant).
    if report.report_type == "PBU":
        procedure_desc = f"{report.report_type} & {report.test_type}"
    else:
        procedure_desc = report.test_type
    ctx["objectives_text"] = (
        f"A Pressure Survey was carried out on {report.date_of_test} in well "
        f"{report.well_name} of {report.field} field to perform "
        f"{procedure_desc} to evaluate the bottom hole pressure and temperature"
    )

    # ── Basic Well Information (three grouped tables) ──────────────────
    ctx["general_information"] = [
        _kv("Company", report.company), _kv("Field", report.field), _kv("Well", report.well_name),
        _kv("DGS/CPS", report.dgs_cps), _kv("Test", report.test_type), _kv("Rig", report.rig),
        _kv("Country/City", report.country_city), _kv("Depth Reference", report.depth_reference),
        _kv("Unit System", report.unit_system),
    ]
    ctx["operation_details"] = [
        _kv("Date of test", report.date_of_test), _kv("Co. Representative", report.company_representative),
        _kv("ABC Representative", report.neos_representative),
    ]
    ctx["well_data"] = [
        _kv("States before test", report.well_state_before_test), _kv("Well type", report.well_type),
        _kv("Fluid type", report.fluid_type), _kv("Completion type", report.completion_type),
        _kv("Well status", report.well_status), _kv("Min restriction", report.min_restriction),
        _kv("Casing size", report.casing_size), _kv("Tubing size", report.tubing_size),
        _kv("Test interval", report.test_interval),
    ]

    # ── Sequence of Events ──────────────────────────────────────────────
    shifts = list(report.shifts.prefetch_related("entries"))
    if report.report_type == "SGS":
        shifts = shifts[:1]
    shift_blocks = []
    for shift in shifts:
        rows = []
        for row in shift.entry_rows():
            e = row["entry"]
            rows.append({
                "date_changed": row["date_changed"], "date": row["date"],
                "sr": e.sr, "start": e.start_display(), "end": e.end_display(),
                "total": e.total_time(), "description": e.description,
            })
        shift_blocks.append({"label": shift.label, "rows": rows})
    ctx["shifts"] = shift_blocks

    # ── Job Information block (sits above the Results Table) ──────────
    ctx["job_info"] = [
        _kv("Well Name", report.well_name), _kv("Date", report.date_of_test),
        _kv("Time RIH", report.time_rih_value()), _kv("Well status", report.well_status),
        _kv("Service", report.test_type), _kv("WHP/Psi", fmt_signed(report.whp_psi_value(), 3)),
    ]

    # ── Results Table (fully pre-formatted so renderers don't repeat
    # decimal/sign logic) ──────────────────────────────────────────────
    formatted_rows = []
    for row in report.station_rows():
        if row["kind"] == "station":
            s = row["station"]
            temp_unit = report.temp_unit
            other_val = s.temp_c() if temp_unit == "F" else s.temp_f()
            other_unit_label = "°C" if temp_unit == "F" else "°F"
            formatted_rows.append({
                "kind": "station",
                "step_no": s.step_no,
                "md_mgl": fmt_signed(s.md_mgl, 0),
                "ft_gl": fmt_signed(s.ft_gl, 1),
                "start": s.start_display(),
                "end": s.end_display(),
                "bar": fmt_signed(s.pressure_bar, 3),
                "psia": fmt_signed(s.pressure_psia, 3) if s.pressure_psia is not None else "needs entry",
                "temp_primary": fmt_signed(s.temp_f() if temp_unit == "F" else s.temp_c(), 3) if s.temp_value is not None else "needs entry",
                "temp_other": (fmt_signed(other_val, 2) + " " + other_unit_label) if s.is_complete else "",
                "is_complete": s.is_complete,
            })
        else:
            formatted_rows.append({
                "kind": "gradient",
                "avg_mgl": fmt_signed(row["avg_mgl"], 0),
                "pressure_gradient": fmt_signed(row["pressure_gradient"], 2),
                "temp_gradient": fmt_signed(row["temp_gradient"], 2),
                "delta_psi_per_min": fmt_signed(row.get("delta_psi_per_min"), 2),
            })
    ctx["station_rows"] = formatted_rows
    ctx["has_stations"] = report.stations.exists()
    ctx["has_enough_for_plots"] = report.stations.count() >= 2

    # ── Images (uploaded slots + gauge-library calibration certs) ──────
    # "cover" is deliberately excluded here -- it's handled above as the
    # cover page's own photo, not as a generic labeled section.
    from .models import AUTO_GENERATED_SECTIONS, GAUGE_LIBRARY_SECTIONS
    sections = []
    for key, label in report.sections():
        if key in {"cover"} | AUTO_GENERATED_SECTIONS:
            continue
        entry = {"key": key, "label": label}
        if key in GAUGE_LIBRARY_SECTIONS:
            gauge = report.bottom_gauge if key == "calibration_bottom" else report.top_gauge
            if gauge and gauge.calibration_certificate:
                entry["image_url"] = gauge.calibration_certificate.url
                entry["image_name"] = gauge.calibration_certificate.name
                entry["heading_suffix"] = gauge.serial_number
            else:
                img = existing_images.get(key)
                entry["image_url"] = img.image.url if img else None
                entry["image_name"] = img.image.name if img else None
        else:
            img = existing_images.get(key)
            entry["image_url"] = img.image.url if img else None
            entry["image_name"] = img.image.name if img else None
            entry["caption"] = img.caption if img else ""
        sections.append(entry)
    ctx["image_sections"] = sections

    ctx["bottom_gauge"] = report.bottom_gauge
    ctx["top_gauge"] = report.top_gauge

    return ctx