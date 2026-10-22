import io

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_POST
from django.utils.http import content_disposition_header
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.permissions import user_can

from .forms import (
    BatteryForm,
    BatteryUsageForm,
    GaugeForm,
    SGSImageForm,
    SGSReportForm,
    SGSSequenceEntryForm,
    SGSShiftForm,
    SGSStationForm,
    SequenceEntryExcelImportForm,
    StationExcelImportForm,
)
from .models import (
    AUTO_GENERATED_SECTIONS,
    GAUGE_LIBRARY_SECTIONS,
    LANDSCAPE_SECTIONS,
    STATUS_CHOICES,
    Battery,
    Gauge,
    SGSImage,
    SGSReport,
    SGSShift,
    SGSStation,
    combine_with_rollover,
)

SORTABLE_FIELDS = {"field": "field", "date": "date_of_test", "client": "client", "status": "status", "well": "well_name"}


def _json_for_script(data):
    """JSON that is safe to print inside a <script> tag: '<', '>' and '&'
    become \\u escapes, so text from an uploaded log file (e.g. a gauge
    serial number containing '</script>') can't break out of the script."""
    import json
    return (json.dumps(data)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def report_list(request):
    sort = request.GET.get("sort", "-created_at")
    search = request.GET.get("q", "").strip()
    direction = ""
    key = sort.lstrip("-")
    if sort.startswith("-"):
        direction = "-"
    order_field = SORTABLE_FIELDS.get(key, "created_at")
    reports = SGSReport.objects.prefetch_related("job_reports__manifest")
    if search:
        from django.db.models import Q
        reports = reports.filter(
            Q(well_name__icontains=search) | Q(field__icontains=search) |
            Q(client__icontains=search) | Q(neos_representative__icontains=search) |
            Q(sor_number__icontains=search)
        )
    reports = reports.order_by(f"{direction}{order_field}")
    from django.db.models import Count
    by_status = dict(SGSReport.objects.values_list("status").annotate(n=Count("pk")))
    stats = {"total": sum(by_status.values()), "open": by_status.get("Open", 0),
             "sent": by_status.get("Sent", 0), "approved": by_status.get("Approved", 0)}
    return render(request, "sgs_report/report_list.html", {
        "reports": reports, "current_sort": sort, "search": search,
        "status_choices": STATUS_CHOICES, "stats": stats,
    })


def report_status_update(request, pk):
    """Lets the report list change status inline without opening the full
    edit form."""
    report = get_object_or_404(SGSReport, pk=pk)
    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status in dict(STATUS_CHOICES):
            report.status = new_status
            report.save(update_fields=["status"])
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(referer)
    return redirect("sgs_report:list")


def report_create(request):
    if request.method == "POST":
        form = SGSReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save()
            messages.success(request, "Report created \u2014 now add station readings and images.")
            return redirect("sgs_report:detail", pk=report.pk)
    else:
        form = SGSReportForm()
    return render(request, "sgs_report/report_form.html", {"form": form})


def report_edit(request, pk):
    report = get_object_or_404(SGSReport, pk=pk)
    if request.method == "POST":
        form = SGSReportForm(request.POST, request.FILES, instance=report)
        if form.is_valid():
            report = form.save()
            _battery_messages(request, report)
            return redirect(f"/sgs-report/{pk}/?tab=common")
    else:
        # Show the actual derived Time RIH value on the form itself,
        # rather than leaving that box blank while it works silently in
        # the background -- this is a display-only convenience; the
        # model still recomputes it fresh from Run start on every save.
        form = SGSReportForm(instance=report, initial={"time_rih": report.time_rih_value()})
    return render(request, "sgs_report/report_form.html", {"form": form, "report": report})


def report_delete(request, pk):
    report = get_object_or_404(SGSReport, pk=pk)
    if request.method == "POST":
        report.delete()
        messages.success(request, "Deleted.")
    return redirect("sgs_report:list")


def _parse_kuster_calibrated_csv(file_obj):
    """Parses the CALIBRATED export from Kuster's software (Export >
    csv, with 'Use comma as delimiter' and 'Include Header' checked) --
    Date/Time, Delta(Hours), Pressure, Temperature, Internal Temp,
    Comments columns. Pressure is already in the gauge's calibrated
    Psia and Temperature is already in \u00b0F (per this export's own header
    lines), so neither needs the raw .log's C-to-F conversion or
    per-gauge calibration factor -- confirmed directly against Kuster's
    own reference table: this parser reproduces 2710.599 psia /
    174.205\u00b0F for the exact same sample with zero adjustment."""
    import datetime as _dt
    raw = file_obj.read()
    text = raw.decode("iso-8859-1") if isinstance(raw, bytes) else raw
    lines = text.splitlines()

    from .client_files import column_positions

    metadata = {}
    header_row_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("dd/mm/yyyy"):
            header_row_idx = i
            break
        if ":" in stripped and "*****" not in stripped:
            key, _, val = stripped.partition(":")
            metadata[key.strip()] = val.strip()

    if header_row_idx is None:
        raise ValueError("Doesn't look like a Kuster .txt export (no 'dd/mm/yyyy  Time' header row found)")

    timestamps, pressures, temps = [], [], []
    # Comma layouts differ ("Date/Time, Delta, Pressure, Temperature, ..." or "Date Time,Pressure,Temperature,"),
    # so the columns are found from the header row's names.
    p_at, t_at = column_positions(lines[header_row_idx])
    for line in lines[header_row_idx + 1:]:
        if not line.strip():
            continue
        if "," in line:
            parts = line.split(",")
            if len(parts) <= max(p_at, t_at):
                continue
            datetime_str = parts[0].strip()
            pressure_text, temperature_text = parts[p_at].strip(), parts[t_at].strip()
        else:
            # "dd/mm/yyyy   hh:mm:ss   pressure   temperature" -- the space-separated export
            parts = line.split()
            if len(parts) < 4:
                continue
            datetime_str = f"{parts[0]} {parts[1]}"
            pressure_text, temperature_text = parts[2], parts[3]
        try:
            date_part, time_part = datetime_str.split()
            dt = _dt.datetime.strptime(f"{date_part} {time_part}", "%d/%m/%Y %H:%M:%S")
        except ValueError:
            continue
        try:
            pressure = float(pressure_text)
            temperature = float(temperature_text)
        except ValueError:
            continue
        timestamps.append(dt)
        pressures.append(pressure)
        temps.append(temperature)

    if not timestamps:
        raise ValueError("No readable data rows found in this file")

    return {
        "metadata": metadata,
        "model_number": metadata.get("Gauge Model Number", "\u2014"),
        "serial_number": metadata.get("Gauge Serial Number", "\u2014"),
        "calibration_date": metadata.get("Date of Last Calibration", "\u2014"),
        "sample_rate": None,
        "start_time": timestamps[0],
        "end_time": timestamps[-1],
        "duration_seconds": (timestamps[-1] - timestamps[0]).total_seconds(),
        "num_samples": len(timestamps),
        "timestamps": timestamps,
        "pressure": pressures,
        "temp1": temps,
        "temp2": temps,
        "already_calibrated": True,
    }


def _released(request, report, kind):
    """Level 3 and admins can always open a report; for everyone else the result / final report has to be approved."""
    from approvals import services as approvals

    return request.user.profile.can_approve or approvals.is_released(report, kind)


def _hold(request, report, kind):
    """A redirect with a clear message when `kind` ('sgs_result' / 'sgs_final') isn't approved yet, else None."""
    if _released(request, report, kind):
        return None
    what = "result and the client data files are" if kind == "sgs_result" else "final report is"
    messages.error(request, f"The {what} released once a Level 3 has approved it. "
                            f"Use \u201cSend for approval\u201d on the {'Build SGS Result' if kind == 'sgs_result' else 'Build Final Report'} tab.")
    return redirect(f"/sgs-report/{report.pk}/?tab={'result' if kind == 'sgs_result' else 'final'}")


def _log_window(file_field):
    """(first, last) sample time of a saved gauge file -- the moment the gauge started and stopped recording."""
    from .gauge_log import parse_gauge_log

    file_field.open("rb")
    try:
        head = file_field.read(64).decode("iso-8859-1", errors="ignore")
        file_field.seek(0)
        parsed = (_parse_kuster_calibrated_csv(file_field) if head.lstrip().startswith("Gauge Serial Number")
                  else parse_gauge_log(file_field))
    finally:
        file_field.close()
    return parsed["start_time"].replace(microsecond=0), parsed["end_time"].replace(microsecond=0)


def _set_times_from_log(request, report, side, run_was_empty):
    """A gauge's connected / disconnected times are the log's first and last sample: they drive the battery hours.
    The run's own dates are filled from them only when they are still empty (they set the dates of events and
    stations)."""
    try:
        first, last = _log_window(getattr(report, f"{side}_gauge_log_file"))
    except Exception as e:                 # an unreadable file is reported by the preview below
        messages.warning(request, f"Couldn't take the {side} gauge times from the log: {e}")
        return
    zone = timezone.get_current_timezone()
    aware = lambda d: timezone.make_aware(d, zone) if timezone.is_naive(d) else d
    setattr(report, f"{side}_gauge_connected_at", aware(first))
    setattr(report, f"{side}_gauge_disconnected_at", aware(last))
    starts = [t for t in (report.bottom_gauge_connected_at, report.top_gauge_connected_at) if t]
    ends = [t for t in (report.bottom_gauge_disconnected_at, report.top_gauge_disconnected_at) if t]
    filled = []
    if run_was_empty["start"] and starts:          # empty before this upload: the run covers every log uploaded
        report.run_start = min(starts)
        filled.append("start")
    if run_was_empty["end"] and ends:
        report.run_end = max(ends)
        filled.append("end")
    report.save()
    report.record_battery_usage()
    text = f"{side.title()} gauge connected {first:%d %b %H:%M:%S}, disconnected {last:%d %b %H:%M:%S} (from the log)."
    if filled:
        text += f" Run {' and '.join(filled)} filled in from it \u2014 events and stations follow that date."
    messages.success(request, text)


def _progress(report, stations, image_slots):
    """The four steps' status line at the top of the report page."""
    from .models import SGSSequenceEntry

    return {
        "logs": sum(1 for side in ("bottom", "top") if getattr(report, f"{side}_gauge_log_file")),
        "stations": len(stations), "stations_done": sum(1 for s in stations if s.is_complete),
        "events": SGSSequenceEntry.objects.filter(shift__report=report).count(),
        "images": sum(1 for slot in image_slots if slot["image"]), "image_total": len(image_slots),
    }


def _client_file_card(report, side, file_field):
    """What the 'Files for the client' card shows for one gauge: the new name, or why there isn't one."""
    from . import client_files

    if not file_field:
        return None
    try:
        file_field.open("rb")
        raw = file_field.read()
        file_field.close()
        made = client_files.prepare(report, side, raw)
    except ValueError as e:
        return {"error": str(e)}
    return {"name": made["name"], "warnings": made["warnings"], "serial": made["serial"], "converted": made["converted"]}


def client_file(request, pk, side):
    """Download one gauge's file the way it goes to the client: new header, new name."""
    from . import client_files

    report = get_object_or_404(SGSReport, pk=pk)
    if side not in client_files.POSITION:
        raise Http404("Unknown gauge position")
    held = _hold(request, report, "sgs_result")
    if held:
        return held
    file_field = getattr(report, f"{side}_gauge_log_file")
    if not file_field:
        messages.error(request, f"No {side} gauge file has been uploaded to this report yet.")
        return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")
    try:
        file_field.open("rb")
        raw = file_field.read()
        file_field.close()
        made = client_files.prepare(report, side, raw)
    except ValueError as e:
        messages.error(request, f"Couldn't prepare the {side} file: {e}")
        return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")
    response = HttpResponse(made["content"], content_type="text/plain; charset=iso-8859-1")
    response["Content-Disposition"] = content_disposition_header(True, made["name"])
    return response


def report_detail(request, pk):
    import datetime as _dt
    report = get_object_or_404(SGSReport, pk=pk)
    shifts = report.shifts.prefetch_related("entries")

    existing_images = {img.section: img for img in report.images.all()}
    image_slots = []
    for key, label in report.sections():
        if key in AUTO_GENERATED_SECTIONS:
            continue
        if key in GAUGE_LIBRARY_SECTIONS:
            gauge = report.bottom_gauge if key == "calibration_bottom" else report.top_gauge
            if gauge:
                continue  # auto-filled from the Gauge Library, no manual upload needed
        image_slots.append({"key": key, "label": label, "image": existing_images.get(key)})

    if request.method == "POST" and "apply_gauge_times" in request.POST:
        import datetime as _dt
        side = request.POST.get("side")
        try:
            start_dt = _dt.datetime.fromisoformat(request.POST["start_iso"]) if request.POST.get("start_iso") else None
            end_dt = _dt.datetime.fromisoformat(request.POST["end_iso"]) if request.POST.get("end_iso") else None
            if side == "bottom":
                report.bottom_gauge_connected_at = start_dt
                report.bottom_gauge_disconnected_at = end_dt
                report.save(update_fields=["bottom_gauge_connected_at", "bottom_gauge_disconnected_at"])
            elif side == "top":
                report.top_gauge_connected_at = start_dt
                report.top_gauge_disconnected_at = end_dt
                report.save(update_fields=["top_gauge_connected_at", "top_gauge_disconnected_at"])
            report.record_battery_usage()
            messages.success(request, f"{side.title()} gauge connect/disconnect times set from the log file.")
            _battery_messages(request, report)
        except (ValueError, KeyError):
            messages.error(request, "Couldn't read those times from the log.")
        return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")

    if request.method == "POST" and "add_shift" in request.POST:
        shift_form = SGSShiftForm(request.POST, report=report)
        if shift_form.is_valid():
            shift = shift_form.save(commit=False)
            shift.report = report
            shift.save()
        else:
            for errors in shift_form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return redirect(f"/sgs-report/{pk}/?tab=final")
    else:
        shift_form = SGSShiftForm(report=report)

    if request.method == "POST" and "add_station" in request.POST:
        import datetime as _dt
        try:
            base_date = report.run_window()[0] or report.date_of_test or _dt.date.today()
            last_station = report.stations.order_by("step_no").last()
            previous_dt = last_station.end_datetime if last_station else None

            start_time_raw = request.POST.get("start_time")
            end_time_raw = request.POST.get("end_time")
            start_dt = None
            end_dt = None
            if start_time_raw:
                start_time = _dt.time.fromisoformat(start_time_raw)
                start_dt = combine_with_rollover(base_date, start_time, previous_dt)
            if end_time_raw:
                end_time = _dt.time.fromisoformat(end_time_raw)
                end_dt = combine_with_rollover(base_date, end_time, start_dt or previous_dt)

            SGSStation.objects.create(
                report=report,
                step_no=int(request.POST.get("step_no") or (report.stations.count() + 1)),
                md_mgl=float(request.POST.get("md_mgl") or 0),
                start_datetime=start_dt,
                end_datetime=end_dt,
                pressure_psia=float(request.POST["pressure_psia"]) if request.POST.get("pressure_psia") else None,
                temp_value=float(request.POST["temp_value"]) if request.POST.get("temp_value") else None,
            )
        except (ValueError, KeyError) as e:
            messages.error(request, f"Couldn't add that station \u2014 check the values entered ({e}).")
        return redirect(f"/sgs-report/{pk}/?tab=result")
    else:
        next_step = report.stations.count() + 1
        station_form = SGSStationForm(initial={"step_no": next_step})

    excel_import_form = StationExcelImportForm()

    if request.method == "POST" and "preview_gauge_log" in request.POST:
        run_was_empty = {"start": not report.run_start, "end": not report.run_end}
        for side, field_name in (("bottom", "bottom_log"), ("top", "top_log")):
            uploaded = request.FILES.get(field_name)
            if not uploaded:
                continue
            model_field = f"{side}_gauge_log_file"
            getattr(report, model_field).save(uploaded.name, uploaded, save=True)
            messages.success(request, f"{side.title()} gauge log saved to this report.")
            _set_times_from_log(request, report, side, run_was_empty)
        _battery_messages(request, report)
        return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")

    if request.method == "POST" and "remove_gauge_log" in request.POST:
        if not user_can(request, "can_delete"):
            messages.error(request, "Only admins can remove a saved gauge log.")
            return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")
        side = request.POST.get("side")
        if side in ("bottom", "top"):
            model_field = f"{side}_gauge_log_file"
            getattr(report, model_field).delete(save=True)
            messages.success(request, f"{side.title()} gauge log removed from this report.")
        return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")

    # Previews are rebuilt from whatever's actually saved on the report
    # every time this page loads -- not just right after an upload -- so
    # switching tabs or reloading never loses them.
    gauge_log_previews = {}
    import json
    from .gauge_log import compute_station_annotations, parse_gauge_log
    stations = list(report.stations.all())
    for side, title in (("bottom", "Bottom Gauge"), ("top", "Top Gauge")):
        file_field = getattr(report, f"{side}_gauge_log_file")
        if not file_field:
            continue
        try:
            file_field.open("rb")
            # Detect the file's actual format by its content, not its
            # extension -- a raw .log file has been uploaded here
            # renamed as .txt before, so the extension alone isn't
            # reliable. The Kuster-calibrated CSV export always starts
            # with "Gauge Serial Number:"; the raw memory-dump .log
            # format starts with "TotalChans=".
            peek = file_field.read(64)
            file_field.seek(0)
            peek_text = peek.decode("iso-8859-1", errors="ignore")
            is_calibrated_csv = peek_text.lstrip().startswith("Gauge Serial Number")

            if is_calibrated_csv:
                parsed = _parse_kuster_calibrated_csv(file_field)
                file_field.close()
                annotations = compute_station_annotations(parsed, stations)
                # Already calibrated Psia and already \u00b0F straight from
                # Kuster's own export -- no C-to-F conversion or
                # per-gauge calibration factor needed here.
                pressure_series = parsed["pressure"]
                temp_f_series = parsed["temp1"]
            else:
                parsed = parse_gauge_log(file_field)
                file_field.close()
                annotations = compute_station_annotations(parsed, stations)
                # temp1 in the raw .log file is Celsius (confirmed directly
                # against a Kuster-software export of the same data: a
                # value the chart showed as 79.003 matched Kuster's own
                # 174.205\u00b0F for that exact second to 3 decimal places,
                # i.e. it's the C-to-F conversion of the same reading) --
                # convert it to Fahrenheit here so the chart matches Kuster
                # and the rest of the report, which are both in \u00b0F.
                temp_f_series = [t * 9 / 5 + 32 for t in parsed["temp1"]]
                # The raw .log pressure column doesn't include the gauge's
                # own calibration -- confirmed directly against 5 rows of a
                # Kuster-software export of this exact file, which showed a
                # perfectly consistent ~1.0055x multiplier (not a simple
                # offset) between raw and calibrated pressure. That factor
                # is specific to each physical gauge's own certificate, so
                # it lives on the Gauge record rather than being hardcoded.
                gauge = getattr(report, f"{side}_gauge", None)
                calibration_factor = gauge.pressure_calibration_factor if gauge else 1.0
                pressure_series = [p * calibration_factor for p in parsed["pressure"]]
            chart_data = {
                "timestamps": [t.isoformat() for t in parsed["timestamps"]],
                "pressure": pressure_series,
                "temperature": temp_f_series,
                "annotations": annotations,
                "label": f"{title} ({parsed['serial_number']})",
            }
            gauge_log_previews[side] = {
                "parsed": parsed,
                "chart_json": _json_for_script(chart_data),
                "filename": file_field.name.rsplit("/", 1)[-1],
                "start_iso": parsed["start_time"].isoformat(),
                "end_iso": parsed["end_time"].isoformat(),
            }
            gauge_log_previews[side]["client_file"] = _client_file_card(report, side, file_field)
        except Exception as e:
            messages.error(request, f"Couldn't read the saved {side} gauge log: {e}")

    last_station = report.stations.order_by("step_no").last()
    suggested_station_time = None
    if last_station and last_station.start_datetime:
        suggested_station_time = (last_station.start_datetime + _dt.timedelta(minutes=10)).strftime("%H:%M:%S")

    shift_suggestions = {}
    for shift in shifts:
        last_entry = shift.entries.order_by("order", "id").last()
        if last_entry and last_entry.start_datetime:
            shift.suggested_time = (last_entry.start_datetime + _dt.timedelta(minutes=30)).strftime("%H:%M:%S")
        else:
            shift.suggested_time = ""

    return render(request, "sgs_report/report_detail.html", {
        "report": report, "shifts": shifts, "shift_form": shift_form, "image_slots": image_slots,
        "station_rows": report.station_rows(), "station_form": station_form,
        "excel_import_form": excel_import_form,
        "tab": request.GET.get("tab", "common"),
        "allow_more_shifts": not (report.report_type == "SGS" and report.shifts.exists()),
        "next_step_no": report.stations.count() + 1,
        "suggested_station_time": suggested_station_time,
        "gauge_log_previews": gauge_log_previews,
        "progress": _progress(report, stations, image_slots),
        "result_released": _released(request, report, "sgs_result"),
        "final_released": _released(request, report, "sgs_final"),
        "battery_summary": report.battery_summary(),
        "all_stations": stations,
    })


def station_import_excel(request, pk):
    report = get_object_or_404(SGSReport, pk=pk)
    if request.method == "POST":
        form = StationExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                import openpyxl
                import datetime as _dt
                wb = openpyxl.load_workbook(form.cleaned_data["excel_file"], data_only=True)
                ws = wb.active
                created = 0
                next_step = report.stations.count() + 1
                base_date = report.run_window()[0] or report.date_of_test or _dt.date.today()

                def to_datetime(val):
                    """Excel gives back a datetime.time for time-formatted
                    cells, a full datetime for date+time cells, or a plain
                    string like '15:05:00' for text cells \u2014 handle all
                    three at HH:MM:SS precision."""
                    if val is None:
                        return None
                    if isinstance(val, _dt.datetime):
                        return val
                    if isinstance(val, _dt.time):
                        return _dt.datetime.combine(base_date, val)
                    if isinstance(val, str):
                        try:
                            return _dt.datetime.combine(base_date, _dt.time.fromisoformat(val.strip()))
                        except ValueError:
                            return None
                    return None

                for row in ws.iter_rows(min_row=2, values_only=True):
                    if not row or row[0] is None:
                        continue
                    # Two supported layouts, auto-detected by checking
                    # whether the SECOND value is time-like:
                    #   Step, Depth (MD-mGL), Start, End   -- 4 columns
                    #   Depth (MD-mGL), Start, End         -- 3 columns, no Step (auto-numbered)
                    if len(row) > 1 and isinstance(row[1], (_dt.time, _dt.datetime)):
                        depth = row[0]
                        start_val = row[1] if len(row) > 1 else None
                        end_val = row[2] if len(row) > 2 else None
                        step_no = None
                    else:
                        step_no, depth = row[0], row[1]
                        start_val = row[2] if len(row) > 2 else None
                        end_val = row[3] if len(row) > 3 else None
                    SGSStation.objects.create(
                        report=report,
                        step_no=int(step_no) if step_no else next_step,
                        md_mgl=float(depth) if depth is not None else 0,
                        start_datetime=to_datetime(start_val),
                        end_datetime=to_datetime(end_val),
                    )
                    next_step += 1
                    created += 1
                messages.success(request, f"Imported {created} station(s) \u2014 now fill in Pressure and Temperature for each.")
            except Exception as e:
                messages.error(request, f"Couldn't read that file: {e}")
    return redirect(f"/sgs-report/{pk}/?tab=result")


@require_POST
def station_delete(request, pk):
    station = get_object_or_404(SGSStation, pk=pk)
    report_pk = station.report_id
    station.delete()
    return redirect(f"/sgs-report/{report_pk}/?tab=result")


def station_edit(request, pk):
    station = get_object_or_404(SGSStation, pk=pk)
    if request.method == "POST":
        form = SGSStationForm(request.POST, instance=station, report=station.report)
        if form.is_valid():
            form.save()
            return redirect(f"/sgs-report/{station.report_id}/?tab=result")
    else:
        form = SGSStationForm(instance=station, report=station.report)
    return render(request, "sgs_report/station_form.html", {"form": form, "station": station})


def station_update_from_chart(request, pk):
    """Fills in an EXISTING station's pressure/temperature from a point
    picked on the Gauge Logs chart -- the person picks which station
    (by step/depth, already set up from the Excel import or manual
    entry) from a dropdown, then clicks the matching point on the
    chart, and that station's readings are filled in directly rather
    than creating a brand new station."""
    import datetime as _dt
    report = get_object_or_404(SGSReport, pk=pk)
    if request.method != "POST":
        return redirect(f"/sgs-report/{pk}/?tab=gauge_logs")
    try:
        station = SGSStation.objects.get(pk=request.POST["station_id"], report=report)
        timestamp = _dt.datetime.fromisoformat(request.POST["timestamp_iso"])
        pressure_psia = float(request.POST["pressure_psia"]) if request.POST.get("pressure_psia") else None
        temp_value = float(request.POST["temp_value"]) if request.POST.get("temp_value") else None
    except (KeyError, ValueError, SGSStation.DoesNotExist):
        return HttpResponse("Invalid data", status=400)
    station.end_datetime = timestamp
    station.pressure_psia = pressure_psia
    station.temp_value = temp_value
    station.save(update_fields=["end_datetime", "pressure_psia", "temp_value"])
    return HttpResponse("OK")


@require_POST
def shift_delete(request, pk):
    shift = get_object_or_404(SGSShift, pk=pk)
    report_pk = shift.report_id
    shift.delete()
    return redirect(f"/sgs-report/{report_pk}/?tab=final")


def entry_add(request, shift_pk):
    from .models import SGSSequenceEntry
    shift = get_object_or_404(SGSShift, pk=shift_pk)
    if request.method == "POST":
        import datetime as _dt
        try:
            base_date = shift.shift_date or shift.report.run_window()[0] or _dt.date.today()
            last_entry = shift.entries.order_by("order", "id").last()
            previous_dt = last_entry.end_datetime if last_entry else None

            start_time_raw = request.POST.get("start_time")
            end_time_raw = request.POST.get("end_time")
            start_dt = None
            end_dt = None
            if start_time_raw:
                start_dt = combine_with_rollover(base_date, _dt.time.fromisoformat(start_time_raw), previous_dt)
            if end_time_raw:
                end_dt = combine_with_rollover(base_date, _dt.time.fromisoformat(end_time_raw), start_dt or previous_dt)

            SGSSequenceEntry.objects.create(
                shift=shift,
                sr=request.POST.get("sr", ""),
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=request.POST.get("description", ""),
                order=shift.entries.count() + 1,
            )
        except (ValueError, KeyError) as e:
            messages.error(request, f"Couldn't add that row \u2014 check the values entered ({e}).")
    return redirect(f"/sgs-report/{shift.report_id}/?tab=final")


@require_POST
def entry_delete(request, pk):
    from .models import SGSSequenceEntry
    entry = get_object_or_404(SGSSequenceEntry, pk=pk)
    report_pk = entry.shift.report_id
    entry.delete()
    return redirect(f"/sgs-report/{report_pk}/?tab=final")


def entry_edit(request, pk):
    from .models import SGSSequenceEntry
    entry = get_object_or_404(SGSSequenceEntry, pk=pk)
    if request.method == "POST":
        form = SGSSequenceEntryForm(request.POST, instance=entry, report=entry.shift.report)
        if form.is_valid():
            form.save()
            return redirect(f"/sgs-report/{entry.shift.report_id}/?tab=final")
    else:
        form = SGSSequenceEntryForm(instance=entry, report=entry.shift.report)
    return render(request, "sgs_report/entry_form.html", {"form": form, "entry": entry})


def entry_import_excel(request, shift_pk):
    """Bulk-imports Sequence of Events rows from Excel, mirroring
    station_import_excel's exact same time-parsing logic (a cell can be
    a datetime.time, a full datetime, or a plain HH:MM:SS string)."""
    from .models import SGSSequenceEntry
    shift = get_object_or_404(SGSShift, pk=shift_pk)
    if request.method == "POST":
        form = SequenceEntryExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                import openpyxl
                import datetime as _dt
                wb = openpyxl.load_workbook(form.cleaned_data["excel_file"], data_only=True)
                ws = wb.active
                created = 0
                base_date = shift.shift_date or shift.report.run_window()[0] or _dt.date.today()
                next_order = shift.entries.count() + 1

                def to_datetime(val):
                    if val is None:
                        return None
                    if isinstance(val, _dt.datetime):
                        return val
                    if isinstance(val, _dt.time):
                        return _dt.datetime.combine(base_date, val)
                    if isinstance(val, str):
                        try:
                            return _dt.datetime.combine(base_date, _dt.time.fromisoformat(val.strip()))
                        except ValueError:
                            return None
                    return None

                for row in ws.iter_rows(min_row=2, values_only=True):
                    if not row or row[0] is None:
                        continue
                    # Expected columns: SR, Start, End, Description
                    sr = row[0]
                    start_val = row[1] if len(row) > 1 else None
                    end_val = row[2] if len(row) > 2 else None
                    description = row[3] if len(row) > 3 else ""
                    SGSSequenceEntry.objects.create(
                        shift=shift,
                        sr=str(sr) if sr is not None else "",
                        start_datetime=to_datetime(start_val),
                        end_datetime=to_datetime(end_val),
                        description=description or "",
                        order=next_order,
                    )
                    next_order += 1
                    created += 1
                messages.success(request, f"Imported {created} event row(s).")
            except Exception as e:
                messages.error(request, f"Couldn't read that file: {e}")
    return redirect(f"/sgs-report/{shift.report_id}/?tab=final")


def image_upload(request, pk, section):
    report = get_object_or_404(SGSReport, pk=pk)
    existing = SGSImage.objects.filter(report=report, section=section).first()
    if request.method == "POST":
        form = SGSImageForm(request.POST, request.FILES, instance=existing)
        if form.is_valid():
            img = form.save(commit=False)
            img.report = report
            img.section = section
            img.save()
    return redirect(f"/sgs-report/{pk}/?tab=final")


@require_POST
def image_delete(request, pk):
    img = get_object_or_404(SGSImage, pk=pk)
    report_pk = img.report_id
    img.delete()
    return redirect(f"/sgs-report/{report_pk}/?tab=final")


# ── Gauge Library ────────────────────────────────────────────────────────

def _battery_messages(request, report):
    """After a job's gauge times are saved: tell the user what each battery gave and what is left."""
    for row in report.battery_summary():
        text = (f"{row['side']} gauge, battery {row['battery'].serial_number}: this job ran {row['hours_text']} and used "
                f"{row['consumed']:,.1f} mAh ({row['draw']:g} mA). {row['remaining']:,.0f} of {row['capacity']:,.0f} mAh left "
                f"({row['percent']:.0f}%, about {row['hours_left']:,.0f} h more).")
        if row["low"]:
            messages.warning(request, text + " Battery is low \u2014 change it before the next job.")
        else:
            messages.info(request, text)


def gauge_list(request):
    gauges = Gauge.objects.all()
    batteries = Battery.objects.all()
    if request.method == "POST" and "add_gauge" in request.POST:
        form = GaugeForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("sgs_report:gauge_list")
    else:
        form = GaugeForm()
    if request.method == "POST" and "add_battery" in request.POST:
        battery_form = BatteryForm(request.POST)
        if battery_form.is_valid():
            battery_form.save()
            return redirect("sgs_report:gauge_list")
    else:
        battery_form = BatteryForm()
    return render(request, "sgs_report/gauge_list.html", {
        "gauges": gauges, "batteries": batteries, "form": form, "battery_form": battery_form,
    })


def gauge_edit(request, pk):
    gauge = get_object_or_404(Gauge, pk=pk)
    if request.method == "POST":
        form = GaugeForm(request.POST, request.FILES, instance=gauge)
        if form.is_valid():
            form.save()
            messages.success(request, "Gauge updated.")
            return redirect("sgs_report:gauge_list")
    else:
        form = GaugeForm(instance=gauge)
    return render(request, "sgs_report/gauge_form.html", {"form": form, "gauge": gauge})


def battery_log_export(request):
    from .models import BatteryUsage

    try:
        import openpyxl
    except ImportError:
        messages.error(request, "Missing library \u2014 run: pip install openpyxl --break-system-packages")
        return redirect("sgs_report:gauge_list")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Battery Log"
    ws.append(["Battery Serial", "Well", "Report Date", "Hours This Job", "Battery Total Hours", "Draw (mA)", "Used This Job (mAh)", "Battery Remaining (mAh)"])
    for usage in BatteryUsage.objects.select_related("battery", "report").order_by("battery__serial_number", "report__date_of_test"):
        ws.append([
            usage.battery.serial_number,
            usage.report.well_name,
            usage.report.date_of_test,
            usage.hours,
            usage.battery.total_hours(),
            usage.draw_ma,
            usage.consumed_mah,
            round(usage.battery.remaining_mah(), 1),
        ])
    for col, width in zip("ABCDEFGH", [16, 16, 14, 16, 18, 12, 20, 22]):
        ws.column_dimensions[col].width = width

    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = content_disposition_header(True, "battery_log.xlsx")
    wb.save(response)
    return response


@require_POST
def gauge_delete(request, pk):
    gauge = get_object_or_404(Gauge, pk=pk)
    gauge.delete()
    return redirect("sgs_report:gauge_list")


@require_POST
def battery_delete(request, pk):
    battery = get_object_or_404(Battery, pk=pk)
    battery.delete()
    return redirect("sgs_report:gauge_list")


def battery_detail(request, pk):
    from .models import BatteryUsage
    battery = get_object_or_404(Battery, pk=pk)
    usages = list(BatteryUsage.objects
                  .filter(battery=battery)
                  .select_related("report")
                  .order_by("report__date_of_test", "report__created_at"))
    running_hours, left = 0, battery.capacity_mah - battery.used_before_mah
    for usage in usages:
        running_hours += usage.hours
        left -= usage.consumed_mah
        usage.running_total = running_hours
        usage.left_after = left
    return render(request, "sgs_report/battery_detail.html", {
        "battery": battery, "usages": usages, "used": battery.used_mah(), "remaining": battery.remaining_mah(),
        "percent": battery.percent_left(), "low": battery.is_low(),
    })


def battery_usage_edit(request, pk):
    """Amend one job's mAh by hand (or go back to the calculated figure)."""
    from .models import BatteryUsage

    usage = get_object_or_404(BatteryUsage.objects.select_related("battery", "report"), pk=pk)
    back = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("sgs_report:battery_detail", args=[usage.battery_id])
    if request.method == "POST":
        if "reset" in request.POST:
            usage.manual = False
            usage.draw_ma = usage.current_draw()
            usage.consumed_mah = usage.calculated_mah()
            usage.save(update_fields=["manual", "draw_ma", "consumed_mah"])
            messages.success(request, f"{usage.report.well_name}: back to the calculated {usage.consumed_mah:,.1f} mAh.")
            return redirect(back)
        form = BatteryUsageForm(request.POST)
        if form.is_valid():
            usage.consumed_mah = round(form.cleaned_data["consumed_mah"], 2)
            usage.manual = True
            usage.save(update_fields=["consumed_mah", "manual"])
            messages.success(request, (f"{usage.report.well_name}: battery {usage.battery.serial_number} set to "
                                       f"{usage.consumed_mah:,.1f} mAh for this job; {usage.battery.remaining_mah():,.0f} mAh left."))
            return redirect(back)
    else:
        form = BatteryUsageForm(initial={"consumed_mah": usage.consumed_mah})
    return render(request, "sgs_report/battery_usage_form.html", {"form": form, "usage": usage, "next": back,
                                                                  "calculated": usage.calculated_mah(), "draw": usage.current_draw()})


def battery_edit(request, pk):
    battery = get_object_or_404(Battery, pk=pk)
    form = BatteryForm(request.POST or None, instance=battery)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Battery {battery.serial_number} updated.")
        return redirect("sgs_report:battery_detail", pk=pk)
    return render(request, "sgs_report/battery_form.html", {"form": form, "battery": battery})


# ── Charts (Word report + Excel export both use matplotlib PNGs for Word;
# Excel uses native charts, built separately below) ───────────────────────

def _chart_title_line(report):
    gauge = report.bottom_gauge or report.top_gauge
    gauge_part = f" Gauge S/N: {gauge.serial_number}" if gauge else ""
    date_str = report.date_of_test.strftime("%d-%b-%y") if report.date_of_test else ""
    return f"{report.well_name} {date_str}{gauge_part}"


def _service_abbreviation(report):
    """Job Information's Service cell needs to fit a narrow column, so
    this shows SGS / FGS / PBU instead of the full test_type text (e.g.
    'Formation Gradient Survey' or 'PBU-Static Gradient Survey')."""
    test_type = (report.test_type or "").upper()
    if "FORMATION" in test_type:
        return "FGS"
    if report.report_type == "PBU":
        return "PBU"
    return "SGS"


def _add_figure_border(fig):
    import matplotlib.patches as mpatches
    fig.patch.set_facecolor("#dbe8f5")
    rect = mpatches.Rectangle((0.005, 0.005), 0.99, 0.99, transform=fig.transFigure,
                               fill=False, edgecolor="#1f3864", linewidth=3)
    fig.patches.append(rect)


def _build_gradient_chart_buffer(report):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [r for r in report.station_rows() if r["kind"] == "gradient" and r["pressure_gradient"] is not None and r["temp_gradient"] is not None]
    depths = [r["avg_mgl"] for r in rows]
    pressure_g = [r["pressure_gradient"] for r in rows]
    temp_g = [r["temp_gradient"] for r in rows]

    fig, ax_bottom = plt.subplots(figsize=(9, 9))
    _add_figure_border(fig)
    ax_bottom.set_facecolor("#dbe8f5")

    # Bottom axis = Temperature, Top axis = Pressure (per reference)
    line2, = ax_bottom.plot(temp_g, depths, marker="o", color="tab:red", label="Temperature degF/ft")
    ax_bottom.set_xlabel("Temperature Gradient (degF/100')", color="tab:red", fontweight="bold")
    ax_bottom.set_xlim(0, 3.0)
    ax_bottom.set_ylabel("Depth MD (mGL)", fontweight="bold")
    ax_bottom.invert_yaxis()
    ax_bottom.grid(True, linestyle="-", color="white", linewidth=1.2)

    ax_right = ax_bottom.twinx()
    ax_right.set_ylim(ax_bottom.get_ylim())
    ax_right.set_ylabel("Depth MD (mGL)", fontweight="bold")

    ax_top = ax_bottom.twiny()
    line1, = ax_top.plot(pressure_g, depths, marker="s", color="tab:blue", label="Pressure psi/ft")
    ax_top.set_xlabel("Pressure Gradient (psi/ft)", color="tab:blue", fontweight="bold")
    ax_top.set_xlim(0, 0.4)

    fig.suptitle("Static Gradient Survey", fontweight="bold", y=0.99, fontsize=13)
    fig.text(0.5, 0.935, _chart_title_line(report), ha="center", color="red", fontweight="bold", fontsize=11)
    fig.text(0.5, 0.895, "P & T Gradient Vs Depth", ha="center", fontweight="bold", fontsize=11)
    fig.legend(handles=[line1, line2], loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.0), frameon=True)
    fig.tight_layout(rect=[0.01, 0.05, 0.99, 0.86])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def _build_tp_chart_buffer(report):
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    stations = [s for s in report.stations.all() if s.is_complete]
    depths = [s.md_mgl for s in stations]
    pressures = [s.pressure_psia for s in stations]
    temps = [s.temp_f() for s in stations]

    fig, ax_bottom = plt.subplots(figsize=(9, 9))
    _add_figure_border(fig)
    ax_bottom.set_facecolor("#dbe8f5")

    # Bottom axis = Temperature, Top axis = Pressure (per reference)
    line2, = ax_bottom.plot(temps, depths, marker="o", color="tab:red", label="degF")
    ax_bottom.set_xlabel("Temperature (degF)", color="tab:red", fontweight="bold")
    ax_bottom.set_ylabel("Depth MD (mGL)", fontweight="bold")
    ax_bottom.invert_yaxis()
    ax_bottom.grid(True, linestyle="-", color="white", linewidth=1.2)
    # Both axes start at 0 -- round the top up to a clean ceiling (next
    # multiple of 20 for temp, next multiple of 500 for pressure) rather
    # than matplotlib's default auto-scaled range, which was starting
    # partway up instead of at 0.
    if temps:
        ax_bottom.set_xlim(0, math.ceil(max(temps) / 20) * 20 or 20)

    ax_right = ax_bottom.twinx()
    ax_right.set_ylim(ax_bottom.get_ylim())
    ax_right.set_ylabel("Depth MD (mGL)", fontweight="bold")

    ax_top = ax_bottom.twiny()
    line1, = ax_top.plot(pressures, depths, marker="s", color="tab:blue", label="Psia")
    ax_top.set_xlabel("Pressure (psia)", color="tab:blue", fontweight="bold")
    if pressures:
        ax_top.set_xlim(0, math.ceil(max(pressures) / 500) * 500 or 500)

    fig.suptitle("Static Gradient Survey", fontweight="bold", y=0.99, fontsize=13)
    fig.text(0.5, 0.935, _chart_title_line(report), ha="center", color="red", fontweight="bold", fontsize=11)
    fig.text(0.5, 0.895, "P & T Vs Depth", ha="center", fontweight="bold", fontsize=11)
    fig.legend(handles=[line1, line2], loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.0), frameon=True)
    fig.tight_layout(rect=[0.01, 0.05, 0.99, 0.86])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def _resize_to_fit(image_field_file, target_w_px, target_h_px):
    from PIL import Image
    from django.core.files.storage import default_storage

    with default_storage.open(image_field_file.name, "rb") as f:
        img = Image.open(f)
        img.load()
    img = img.convert("RGBA")
    scale = min(target_w_px / img.width, target_h_px / img.height)
    new_w, new_h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (target_w_px, target_h_px), (255, 255, 255, 0))
    canvas.paste(resized, ((target_w_px - new_w) // 2, (target_h_px - new_h) // 2), resized)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── Excel export ──────────────────────────────────────────────────────────

def export_excel(request, pk):
    report = get_object_or_404(SGSReport, pk=pk)
    held = _hold(request, report, "sgs_result")
    if held:
        return held

    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    except ImportError:
        messages.error(request, "Missing library \u2014 run: pip install openpyxl --break-system-packages")
        return redirect(f"/sgs-report/{pk}/?tab=result")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gradient Table (POOH-RIH)"

    VALUE_FMT = '0.000;[Red](0.000)'     # 3 decimals, negatives red in parentheses
    GRAD_FMT = '0.00;[Red](0.00)'        # 2 decimals, negatives red in parentheses
    DATA_FONT = Font(color="1F1F1F")     # entered/raw data
    COMPUTED_FONT = Font(color="1F6FEB", italic=True)  # avg/gradient rows \u2014 visually distinct
    GREY_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    THIN_BORDER = Border(*(Side(style="thin", color="999999"),) * 4)

    ws["D4"] = "Job Information "
    ws["D4"].font = Font(bold=True)
    ws["D4"].fill = GREY_FILL
    ws.merge_cells("D4:E4")
    ws["D4"].alignment = Alignment(horizontal="center")
    job_info_rows = [
        ("Well Name", report.well_name), ("Date ", report.date_of_test),
        ("Time RIH", report.time_rih_value()), ("Well status", report.well_status),
        ("Service", _service_abbreviation(report)), ("WHP/Psi", report.whp_psi_value()),
    ]
    for i, (label, value) in enumerate(job_info_rows):
        label_cell = ws.cell(5 + i, 4, label)
        label_cell.fill = GREY_FILL
        label_cell.font = Font(bold=True)
        label_cell.border = THIN_BORDER
        value_cell = ws.cell(5 + i, 5, value)
        value_cell.border = THIN_BORDER

    header_row1, header_row2 = 14, 15
    ws.cell(header_row1, 1, "Step NO")
    ws.cell(header_row1, 2, "Depths ")
    ws.merge_cells(start_row=header_row1, start_column=2, end_row=header_row1, end_column=4)
    ws.cell(header_row1, 5, "Station Start\n Time")
    ws.cell(header_row1, 6, "Station End\n Time")
    ws.cell(header_row1, 7, "Pressure")
    ws.merge_cells(start_row=header_row1, start_column=7, end_row=header_row1, end_column=8)
    ws.cell(header_row1, 9, "Delta Psi\nper min")
    ws.merge_cells(start_row=header_row1, start_column=9, end_row=header_row2, end_column=9)
    ws.cell(header_row1, 10, "Temperature ")
    ws.merge_cells(start_row=header_row1, start_column=10, end_row=header_row1, end_column=11)
    ws.cell(header_row1, 12, "Gradients")
    ws.merge_cells(start_row=header_row1, start_column=12, end_row=header_row1, end_column=13)

    sub_labels = {2: "MD-mGL", 3: "ft GL", 4: "Average", 5: "HH:MM:SS", 6: "HH:MM:SS", 7: "Bar",
                  8: "Psia", 10: "degF", 11: "degC", 12: "Pressure\npsi/ft", 13: "Temperature\ndegF/ft"}
    for col, text in sub_labels.items():
        ws.cell(header_row2, col, text)

    for row in (header_row1, header_row2):
        for col in range(1, 14):
            cell = ws.cell(row, col)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            cell.fill = GREY_FILL
            cell.border = THIN_BORDER

    r = header_row2 + 1
    for row in report.station_rows():
        if row["kind"] == "station":
            s = row["station"]
            ws.cell(r, 1, s.step_no).font = DATA_FONT
            ws.cell(r, 2, s.md_mgl).font = DATA_FONT
            c = ws.cell(r, 3, s.ft_gl); c.font = DATA_FONT; c.number_format = VALUE_FMT
            ws.cell(r, 5, s.start_display()).font = DATA_FONT
            ws.cell(r, 6, s.end_display()).font = DATA_FONT
            for col, val in ((7, s.pressure_bar), (8, s.pressure_psia)):
                c = ws.cell(r, col, val); c.font = DATA_FONT; c.number_format = VALUE_FMT
            for col, val in ((10, s.temp_f()), (11, s.temp_c())):
                c = ws.cell(r, col, val); c.font = DATA_FONT; c.number_format = VALUE_FMT
        else:
            # Computed/gradient rows get a full grey row fill (not just
            # colored font) to visually set them apart from raw station
            # rows, matching the reference layout.
            for col in range(1, 14):
                ws.cell(r, col).fill = GREY_FILL
            c = ws.cell(r, 4, row["avg_mgl"]); c.font = COMPUTED_FONT; c.number_format = VALUE_FMT
            c = ws.cell(r, 9, row.get("delta_psi_per_min")); c.font = COMPUTED_FONT; c.number_format = GRAD_FMT
            c = ws.cell(r, 12, row["pressure_gradient"]); c.font = COMPUTED_FONT; c.number_format = GRAD_FMT
            c = ws.cell(r, 13, row["temp_gradient"]); c.font = COMPUTED_FONT; c.number_format = GRAD_FMT
        for col in range(1, 14):
            ws.cell(r, col).border = THIN_BORDER
        r += 1

    for col, width in zip("ABCDEFGHIJKLM", [8, 9, 9, 13, 12, 12, 9, 9, 10, 8, 8, 11, 11]):
        ws.column_dimensions[col].width = width

    if report.stations.count() >= 2:
        from openpyxl.drawing.image import Image as XLImage
        ws2 = wb.create_sheet("Gradients Plot")
        ws2.add_image(XLImage(_build_gradient_chart_buffer(report)), "B2")

    if report.stations.exists():
        from openpyxl.drawing.image import Image as XLImage
        ws3 = wb.create_sheet("T&P Plot")
        ws3.add_image(XLImage(_build_tp_chart_buffer(report)), "B2")

    date_str = report.date_of_test.strftime("%Y%m%d") if report.date_of_test else "unknown-date"
    type_token = "FGS" if "FGS" in (report.test_type or "").upper() else report.report_type  # SGS or PBU
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"{report.well_name}_{type_token}_Result_{date_str}.xlsx".replace(" ", "_")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    wb.save(response)
    return response


# ── PDF report generation (ReportLab, self-contained, no template) ───────

def generate_pdf(request, pk):
    from .report_pdf import build_pdf
    report = get_object_or_404(SGSReport, pk=pk)
    held = _hold(request, report, "sgs_final")
    if held:
        return held
    try:
        buffer = build_pdf(report)
    except Exception as e:
        messages.error(request, f"Couldn't generate PDF: {e}")
        return redirect(f"/sgs-report/{pk}/?tab=final")

    date_str = report.date_of_test.strftime("%Y%m%d") if report.date_of_test else "unknown-date"
    type_token = "FGS" if "FGS" in (report.test_type or "").upper() else report.report_type
    filename = f"{report.well_name}_{type_token}_Final-Report_{date_str}.pdf".replace(" ", "_")
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response


# ── Word report generation ────────────────────────────────────────────────



def generate_docx(request, pk):
    from django.core.files.base import ContentFile
    from .report_docx import build_docx
    report = get_object_or_404(SGSReport, pk=pk)
    held = _hold(request, report, "sgs_final")
    if held:
        return held
    try:
        buffer = build_docx(report)
    except Exception as e:
        messages.error(request, f"Couldn't generate the Word document: {e}")
        return redirect(f"/sgs-report/{pk}/?tab=final")

    date_str = report.date_of_test.strftime("%Y%m%d") if report.date_of_test else "unknown-date"
    type_token = "FGS" if "FGS" in (report.test_type or "").upper() else report.report_type
    filename = f"{report.well_name}_{type_token}_Final-Report_{date_str}.docx".replace(" ", "_")

    report.generated_file.save(filename, ContentFile(buffer.read()), save=False)
    report.generated_at = timezone.now()
    report.save()

    messages.success(request, "Report generated.")
    return redirect(f"/sgs-report/{pk}/?tab=final")
