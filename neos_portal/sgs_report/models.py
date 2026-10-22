import re

from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models

REPORT_TYPE_CHOICES = [("PBU", "PBU"), ("SGS", "SGS/FGS")]
TEMP_UNIT_CHOICES = [("F", "Fahrenheit (\u00b0F)"), ("C", "Celsius (\u00b0C)")]
STATUS_CHOICES = [("Open", "Open"), ("Sent", "Sent"), ("Approved", "Approved")]
TIME_DISPLAY_CHOICES = [("24", "24-hour"), ("12", "12-hour")]

# (key, label, {report types this section applies to})
ALL_SECTIONS = [
    ("cover", "Cover Photo", {"PBU", "SGS"}),
    ("results_table", "Results Table", {"PBU", "SGS"}),           # auto-generated
    ("result_plots", "Result Plots", {"PBU", "SGS"}),             # auto-generated, both charts on one page
    ("overall_gauge_plot", "Overall Downhole Memory Gauge Plot", {"PBU", "SGS"}),
    ("gauges_comparison", "Down Hole Memory Gauges Overall Comparison", {"PBU", "SGS"}),
    ("pbu_1", "Pressure Build-up (Bottom Gauge)", {"PBU"}),
    ("pbu_2", "Pressure Build-up (Bottom Gauge and Top Gauge)", {"PBU"}),
    ("completion_diagram", "Well Completion Diagram", {"PBU", "SGS"}),
    ("wellhead_picture", "Wellhead Picture", {"PBU", "SGS"}),
    ("string_diagram", "Slickline Memory Gauges String Diagram", {"PBU", "SGS"}),
    ("pce_schematic", "PCE Schematic", {"PBU", "SGS"}),
    ("calibration_bottom", "Calibration Bottom Gauge", {"PBU", "SGS"}),   # auto-filled from Gauge Library
    ("calibration_top", "Calibration Top Gauge", {"PBU", "SGS"}),        # auto-filled from Gauge Library
]

AUTO_GENERATED_SECTIONS = {"results_table", "result_plots"}
GAUGE_LIBRARY_SECTIONS = {"calibration_bottom", "calibration_top"}
LANDSCAPE_SECTIONS = {"results_table", "result_plots", "overall_gauge_plot", "gauges_comparison", "pbu_1", "pbu_2"}


def sections_for(report_type):
    return [(key, label) for key, label, types in ALL_SECTIONS if report_type in types]


def combine_with_rollover(base_date, time_value, previous_dt):
    """Combines a plain time with a date, automatically rolling to the
    next day if it would otherwise be earlier than the previous
    entry \u2014 so the user only ever picks a date once and just enters
    times after that."""
    import datetime as _dt
    from django.utils import timezone
    if previous_dt is not None and timezone.is_aware(previous_dt):
        previous_dt = timezone.localtime(previous_dt).replace(tzinfo=None)
    if previous_dt is None:
        return _dt.datetime.combine(base_date, time_value)
    candidate = _dt.datetime.combine(previous_dt.date(), time_value)
    if candidate < previous_dt:
        candidate += _dt.timedelta(days=1)
    return candidate


def fmt_signed(value, digits=3):
    """Formats a number to a fixed number of decimals, rendering negative
    values in parentheses instead of a minus sign (e.g. -4.2 -> (4.200)).
    Returns "" for None."""
    if value is None:
        return ""
    if value < 0:
        return f"({abs(value):.{digits}f})"
    return f"{value:.{digits}f}"


DEFAULT_CAPACITY_MAH = 13000.0     # a battery pack, as measured from the field sheet
DEFAULT_DRAW_MA = 8.49             # the average current a running gauge takes: total mAh / total hours in the battery sheet
LOW_BATTERY_FRACTION = 0.15        # warn when less than this share of the capacity is left


def hours_text(hours):
    """4.5667 -> '4 h 34 min'"""
    total = int(round(hours * 60))
    return f"{total // 60} h {total % 60:02d} min"


class Battery(models.Model):
    serial_number = models.CharField(max_length=100, unique=True)
    capacity_mah = models.FloatField(
        default=DEFAULT_CAPACITY_MAH, verbose_name="Capacity (mAh)",
        help_text="What a full battery holds -- about 13,000 mAh.",
    )
    used_before_mah = models.FloatField(
        default=0.0, verbose_name="Already used before (mAh)",
        help_text="Consumption recorded before this battery was added here (from your battery sheet). Leave 0 for a new battery.",
    )

    class Meta:
        ordering = ["serial_number"]

    def __str__(self):
        return self.serial_number

    def total_hours(self):
        from django.db.models import Sum
        return self.usages.aggregate(total=Sum("hours"))["total"] or 0

    def used_mah(self):
        """Everything this battery has given: what was used before + every job logged here."""
        from django.db.models import Sum
        return self.used_before_mah + (self.usages.aggregate(total=Sum("consumed_mah"))["total"] or 0)

    def remaining_mah(self):
        return self.capacity_mah - self.used_mah()

    def percent_left(self):
        return max(0.0, min(100.0, 100.0 * self.remaining_mah() / self.capacity_mah)) if self.capacity_mah else 0.0

    def is_low(self):
        return self.capacity_mah > 0 and self.remaining_mah() < LOW_BATTERY_FRACTION * self.capacity_mah


class Gauge(models.Model):
    """A memory gauge in the library. Each physical gauge (a 'top' gauge and
    a 'bottom' gauge are two separate physical units) has exactly ONE
    battery paired to it \u2014 its calibration certificate is stored once here
    and pulled into every report that uses it."""
    serial_number = models.CharField(max_length=100, unique=True)
    calibration_certificate = models.ImageField(upload_to="sgs_reports/gauge_calibrations/")
    pressure_calibration_factor = models.FloatField(
        default=1.0,
        help_text=(
            "Multiplier applied to this gauge's raw .log pressure readings "
            "to match its calibration certificate (e.g. 1.0054528). Leave "
            "at 1.0 if the raw readings already match the certificate."
        ),
    )
    battery = models.ForeignKey(
        Battery, null=True, blank=True, on_delete=models.SET_NULL, related_name="gauges",
        help_text="The single battery currently paired with this gauge",
    )
    draw_ma = models.FloatField(
        default=DEFAULT_DRAW_MA, verbose_name="Current draw (mA)",
        help_text="Average current this gauge takes while running; hours connected x this = mAh used from the battery.",
    )

    class Meta:
        ordering = ["serial_number"]

    def __str__(self):
        return self.serial_number


class BatteryUsage(models.Model):
    """Hours a specific battery ran on a specific report/job, calculated
    automatically from that report's run start/end \u2014 summed per-battery
    to know when it needs replacing."""
    battery = models.ForeignKey(Battery, on_delete=models.CASCADE, related_name="usages")
    report = models.ForeignKey("SGSReport", on_delete=models.CASCADE, related_name="battery_usages")
    hours = models.FloatField()
    # Worked out when the job is saved, and kept: changing a gauge's draw later doesn't rewrite old jobs.
    draw_ma = models.FloatField(default=DEFAULT_DRAW_MA)
    consumed_mah = models.FloatField(default=0.0)
    manual = models.BooleanField(
        default=False, help_text="The mAh were typed in for this job; saving the report again doesn't recalculate them.")

    class Meta:
        unique_together = ("battery", "report")

    def __str__(self):
        return f"{self.battery} \u00b7 {self.report} \u00b7 {self.hours}h"

    def current_draw(self):
        """The draw a recalculation would use: the paired gauge's setting now (else the one saved with the job)."""
        for gauge in (self.report.bottom_gauge, self.report.top_gauge):
            if gauge and gauge.battery_id == self.battery_id:
                return gauge.draw_ma
        return self.draw_ma

    def calculated_mah(self):
        return round(self.hours * self.current_draw(), 2)


class SGSReport(models.Model):
    report_type = models.CharField(max_length=10, choices=REPORT_TYPE_CHOICES, default="PBU")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Open")
    client = models.CharField(max_length=150, blank=True, default="")
    temp_unit = models.CharField(
        max_length=1, choices=TEMP_UNIT_CHOICES, default="F",
        help_text="Unit you'll enter station temperature readings in \u2014 the other is calculated automatically.",
    )
    time_display = models.CharField(
        max_length=2, choices=TIME_DISPLAY_CHOICES, default="24",
        help_text="How times are SHOWN in the generated report \u2014 always stored/entered as 24-hour.",
    )
    client_logo = models.ImageField(
        upload_to="sgs_reports/client_logos/", blank=True, null=True,
        help_text="Shown on the right of every page header, at a fixed size regardless of the image you upload.",
    )

    # Run start/end \u2014 drives automatic battery-hours calculation and lets
    # the Sequence of Events / stations handle overnight (day-crossing) jobs
    run_start = models.DateTimeField(null=True, blank=True, verbose_name="Run start (date & time)")
    run_end = models.DateTimeField(null=True, blank=True, verbose_name="Run end (date & time)")

    # Battery hours are tracked from when the SUPERVISOR actually connected
    # the battery to each gauge \u2014 not the overall job run_start/run_end
    # above, since a gauge can be connected before/after the official job
    # window (e.g. left running during rig-up, or pulled early).
    bottom_gauge_connected_at = models.DateTimeField(null=True, blank=True, verbose_name="Bottom gauge connected")
    bottom_gauge_disconnected_at = models.DateTimeField(null=True, blank=True, verbose_name="Bottom gauge disconnected")
    top_gauge_connected_at = models.DateTimeField(null=True, blank=True, verbose_name="Top gauge connected")
    top_gauge_disconnected_at = models.DateTimeField(null=True, blank=True, verbose_name="Top gauge disconnected")

    # General Information
    well_name = models.CharField(max_length=100)
    field = models.CharField(max_length=150, blank=True, default="")
    company = models.CharField(max_length=150, blank=True, default="", help_text="e.g. Client A")
    sor_number = models.CharField(max_length=50, blank=True, default="", verbose_name="SOR Number")
    dgs_cps = models.CharField(max_length=100, blank=True, default="", verbose_name="DGS/CPS")
    test_type = models.CharField(max_length=150, blank=True, default="PBU-Static Gradient Survey")
    rig = models.CharField(max_length=100, blank=True, default="")
    country_city = models.CharField(max_length=150, blank=True, default="")
    depth_reference = models.CharField(max_length=100, blank=True, default="GL (Ground Level)")
    unit_system = models.CharField(max_length=100, blank=True, default="Field System")

    # Operation Details
    date_of_test = models.DateField(null=True, blank=True)
    company_representative = models.CharField(max_length=150, blank=True, default="")
    neos_representative = models.CharField(max_length=150, blank=True, default="")

    # Well Data
    well_state_before_test = models.CharField(max_length=100, blank=True, default="")
    well_type = models.CharField(max_length=100, blank=True, default="")
    fluid_type = models.CharField(max_length=100, blank=True, default="")
    completion_type = models.CharField(max_length=100, blank=True, default="")
    well_status = models.CharField(max_length=100, blank=True, default="")
    min_restriction = models.CharField(max_length=150, blank=True, default="")
    casing_size = models.CharField(max_length=100, blank=True, default="")
    tubing_size = models.CharField(max_length=100, blank=True, default="")
    test_interval = models.CharField(max_length=255, blank=True, default="")

    # Excel export's "Job Information" block
    time_rih = models.CharField(
        max_length=20, blank=True, default="", verbose_name="Time RIH",
        help_text="Only used as a fallback if Run start (above) isn't set \u2014 normally derived automatically.",
    )
    whp_psi = models.FloatField(null=True, blank=True, verbose_name="WHP/Psi")

    # Gauge Library selections \u2014 drive the calibration pages and battery
    # hours automatically. Each gauge has its own single battery.
    bottom_gauge = models.ForeignKey(
        Gauge, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_as_bottom",
    )
    top_gauge = models.ForeignKey(
        Gauge, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_as_top",
    )

    # Raw memory-gauge .log files, saved permanently once uploaded so the
    # parsed preview survives tab switches and page reloads instead of
    # only existing for the one request that uploaded them.
    bottom_gauge_log_file = models.FileField(upload_to="sgs_reports/gauge_logs/", blank=True, null=True)
    top_gauge_log_file = models.FileField(upload_to="sgs_reports/gauge_logs/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    generated_file = models.FileField(upload_to="sgs_reports/generated/", blank=True, null=True)
    generated_at = models.DateTimeField(null=True, blank=True)

    approvals = GenericRelation("approvals.Approval")      # waiting / approved / rejected, see approvals/

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Date of test duplicated Run start's own date, forcing it to be
        # entered twice -- keep it silently in sync with Run start
        # instead, since date_of_test still needs to be a real stored
        # field for the sorting/ordering queries elsewhere that use it.
        if self.run_start:
            self.date_of_test = self.run_window()[0]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.well_name} \u2014 {self.date_of_test}"

    def sections(self):
        return sections_for(self.report_type)

    def run_window(self):
        """(first day, last day) of the run in local time. Either may be None; last is None until Run end is set.
        Local days matter: a run that starts at 01:00 is on that calendar day here, not on the UTC one."""
        from django.utils import timezone

        def day(dt):
            if not dt:
                return None
            return timezone.localtime(dt).date() if timezone.is_aware(dt) else dt.date()

        return day(self.run_start), day(self.run_end)

    def run_hours(self):
        if self.run_start and self.run_end:
            return round((self.run_end - self.run_start).total_seconds() / 3600, 2)
        return None

    def time_rih_value(self):
        """Time RIH is the same moment as Run start -- derived
        automatically so it never has to be typed in twice. Falls back
        to the manually-entered time_rih field only if Run start isn't
        set yet."""
        if self.run_start:
            from django.utils import timezone
            dt = self.run_start
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            return dt.strftime("%H:%M:%S")
        return self.time_rih

    def whp_psi_value(self):
        """WHP/Psi is the pressure reading from the LAST station in the
        Results Table (the shallowest/final reading) \u2014 not manually
        entered."""
        last_station = self.stations.order_by("step_no").last()
        return last_station.pressure_psia if last_station else self.whp_psi

    def bottom_gauge_hours(self):
        if self.bottom_gauge_connected_at and self.bottom_gauge_disconnected_at:
            return round((self.bottom_gauge_disconnected_at - self.bottom_gauge_connected_at).total_seconds() / 3600, 2)
        return None

    def top_gauge_hours(self):
        if self.top_gauge_connected_at and self.top_gauge_disconnected_at:
            return round((self.top_gauge_disconnected_at - self.top_gauge_connected_at).total_seconds() / 3600, 2)
        return None

    def record_battery_usage(self):
        """Called on save \u2014 logs the ACTUAL time each battery spent
        connected (from the supervisor-recorded connect/disconnect times
        on each gauge, not the overall job run_start/run_end) against
        whichever battery is currently paired with that gauge, together
        with what that cost the battery in mAh (hours x the gauge's draw),
        so Battery.total_hours() and remaining_mah() stay accurate."""
        def exact(start, end):          # to the second: 4 h 34 min is 4.5667 h, not the 4.57 shown elsewhere
            return round((end - start).total_seconds() / 3600, 4) if start and end else None

        for gauge, hours in (
            (self.bottom_gauge, exact(self.bottom_gauge_connected_at, self.bottom_gauge_disconnected_at)),
            (self.top_gauge, exact(self.top_gauge_connected_at, self.top_gauge_disconnected_at)),
        ):
            if not (gauge and gauge.battery and hours is not None):
                continue
            usage = BatteryUsage.objects.filter(battery=gauge.battery, report=self).first()
            if usage is None:
                BatteryUsage.objects.create(battery=gauge.battery, report=self, hours=hours, draw_ma=gauge.draw_ma,
                                            consumed_mah=round(hours * gauge.draw_ma, 2))
            elif usage.manual:                      # an amended figure stays; only the hours follow the report
                BatteryUsage.objects.filter(pk=usage.pk).update(hours=hours)
            else:
                BatteryUsage.objects.filter(pk=usage.pk).update(
                    hours=hours, draw_ma=gauge.draw_ma, consumed_mah=round(hours * gauge.draw_ma, 2))

    def battery_summary(self):
        """For each gauge with a battery and logged hours: what this job cost and what is left."""
        rows = []
        for side, gauge in (("Bottom", self.bottom_gauge), ("Top", self.top_gauge)):
            if not (gauge and gauge.battery):
                continue
            usage = self.battery_usages.filter(battery=gauge.battery).first()
            if not usage:
                continue
            battery = gauge.battery
            rows.append({
                "side": side, "battery": battery, "usage": usage,
                "hours_text": hours_text(usage.hours), "consumed": usage.consumed_mah, "draw": usage.draw_ma,
                "manual": usage.manual,
                "remaining": battery.remaining_mah(), "capacity": battery.capacity_mah,
                "percent": battery.percent_left(), "low": battery.is_low(),
                "hours_left": battery.remaining_mah() / usage.draw_ma if usage.draw_ma else None,
            })
        return rows

    def format_time(self, dt):
        """Used by the Sequence of Events table \u2014 stays HH:MM (no seconds)."""
        if not dt:
            return ""
        from django.utils import timezone
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        return dt.strftime("%I:%M %p").lstrip("0") if self.time_display == "12" else dt.strftime("%H:%M")

    def format_time_seconds(self, dt):
        """Used by the SGS Result stations table \u2014 always HH:MM:SS so
        accurate pressure/temperature readings can be lined up to the
        second."""
        if not dt:
            return ""
        from django.utils import timezone
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        return dt.strftime("%I:%M:%S %p").lstrip("0") if self.time_display == "12" else dt.strftime("%H:%M:%S")

    def station_rows(self):
        """Builds the full Results Table row list, alternating each raw
        station reading with a computed "average/gradient" sub-row between
        it and the next one: average MD-mGL of the two stations, pressure
        gradient = 0.3048 * delta(Psia) / delta(mGL), temperature gradient
        = delta(degF) / delta(ft GL) * 100, and delta Psi per minute between
        the two stations' start times.
        """
        stations = list(self.stations.all())
        rows = []
        for i, s in enumerate(stations):
            rows.append({"kind": "station", "station": s})
            if i + 1 < len(stations):
                nxt = stations[i + 1]
                avg_mgl = (s.md_mgl + nxt.md_mgl) / 2
                delta_psi_per_min = None
                if s.is_complete and nxt.is_complete:
                    delta_psia = s.pressure_psia - nxt.pressure_psia
                    delta_mgl = s.md_mgl - nxt.md_mgl
                    delta_degf = s.temp_f() - nxt.temp_f()
                    delta_ft = s.ft_gl - nxt.ft_gl
                    pressure_gradient = (0.3048 * delta_psia / delta_mgl) if delta_mgl else None
                    temp_gradient = (delta_degf / delta_ft * 100) if delta_ft else None
                    if s.start_datetime and nxt.start_datetime:
                        delta_minutes = abs((nxt.start_datetime - s.start_datetime).total_seconds()) / 60
                        if delta_minutes:
                            delta_psi_per_min = delta_psia / delta_minutes
                else:
                    pressure_gradient = None
                    temp_gradient = None
                rows.append({
                    "kind": "gradient", "avg_mgl": avg_mgl,
                    "pressure_gradient": pressure_gradient, "temp_gradient": temp_gradient,
                    "delta_psi_per_min": delta_psi_per_min,
                })
        return rows


class SGSStation(models.Model):
    """One raw survey reading/station. Start/end are full date+time so
    overnight (day-crossing) jobs compute correctly. Everything besides
    depth/times/pressure/temperature is computed."""
    report = models.ForeignKey(SGSReport, on_delete=models.CASCADE, related_name="stations")
    step_no = models.PositiveIntegerField()
    md_mgl = models.FloatField(verbose_name="Depth MD (mGL)")
    start_datetime = models.DateTimeField(null=True, blank=True, verbose_name="Station start")
    end_datetime = models.DateTimeField(null=True, blank=True, verbose_name="Station end")
    pressure_psia = models.FloatField(null=True, blank=True, verbose_name="Pressure (Psia)")
    temp_value = models.FloatField(null=True, blank=True, verbose_name="Temperature (in the report's chosen unit)")

    class Meta:
        ordering = ["step_no"]

    def __str__(self):
        return f"{self.report} \u00b7 Step {self.step_no}"

    @property
    def is_complete(self):
        return self.pressure_psia is not None and self.temp_value is not None

    @property
    def ft_gl(self):
        return self.md_mgl / 0.3048

    @property
    def pressure_bar(self):
        return self.pressure_psia / 14.5038 if self.pressure_psia is not None else None

    def temp_f(self):
        if self.temp_value is None:
            return None
        if self.report.temp_unit == "F":
            return self.temp_value
        return self.temp_value * 9 / 5 + 32

    def temp_c(self):
        if self.temp_value is None:
            return None
        if self.report.temp_unit == "C":
            return self.temp_value
        return (self.temp_value - 32) * 5 / 9

    def start_display(self):
        return self.report.format_time_seconds(self.start_datetime)

    def end_display(self):
        return self.report.format_time_seconds(self.end_datetime)


class SGSShift(models.Model):
    report = models.ForeignKey(SGSReport, on_delete=models.CASCADE, related_name="shifts")
    label = models.CharField(max_length=150, help_text='e.g. "Day shift 01-02-2026"')
    shift_date = models.DateField(null=True, blank=True, help_text="Set once \u2014 each row below only needs a time, and automatically rolls to the next day if it crosses midnight.")
    order = models.PositiveIntegerField(default=0, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.label

    def summary(self):
        """For the shift's collapsed header: how many events, and from when to when."""
        entries = list(self.entries.all())
        starts = [e.start_datetime for e in entries if e.start_datetime]
        ends = [e.end_datetime for e in entries if e.end_datetime]
        return {
            "count": len(entries),
            "first": self.report.format_time(min(starts)) if starts else "",
            "last": self.report.format_time(max(ends)) if ends else "",
        }

    def entry_rows(self):
        """Entries with a 'date_changed' flag on the first entry of each new
        calendar date, so the template can insert a highlighted date banner
        \u2014 matching jobs that cross midnight."""
        entries = list(self.entries.all())
        rows = []
        last_date = None
        for e in entries:
            entry_date = e.start_datetime.date() if e.start_datetime else None
            date_changed = entry_date is not None and entry_date != last_date
            rows.append({"entry": e, "date_changed": date_changed and last_date is not None, "date": entry_date})
            if entry_date is not None:
                last_date = entry_date
        return rows


class SGSSequenceEntry(models.Model):
    shift = models.ForeignKey(SGSShift, on_delete=models.CASCADE, related_name="entries")
    sr = models.CharField(max_length=20, blank=True, default="")
    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.shift.label} \u00b7 SR{self.sr}"

    def total_time(self):
        if self.start_datetime and self.end_datetime:
            delta = self.end_datetime - self.start_datetime
            total_minutes = int(delta.total_seconds() // 60)
            hours, minutes = divmod(total_minutes, 60)
            return f"{hours}:{minutes:02d}"
        return ""

    def start_display(self):
        return self.shift.report.format_time(self.start_datetime)

    def end_display(self):
        return self.shift.report.format_time(self.end_datetime)


class SGSImage(models.Model):
    report = models.ForeignKey(SGSReport, on_delete=models.CASCADE, related_name="images")
    section = models.CharField(max_length=30)
    image = models.ImageField(upload_to="sgs_reports/images/")
    caption = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        unique_together = ("report", "section")

    def __str__(self):
        return f"{self.report} \u00b7 {self.section}"
