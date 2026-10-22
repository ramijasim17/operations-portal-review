"""
Parses Kuster gauge memory-dump .log files (header metadata + [Data]
section: a POR reference line, then one row per sample of
pressure, temp1, temp2) and builds a quick preview chart from them.

File format (confirmed against a real Ru-677 gauge log):
    TotalChans=3\r\n
    ModelNumber=Quantum Quartz\r\n
    SerialNumber=D8700\r\n
    ...
    [Data]\r\n
    POR, 46135.635359, 1\r\n
    -0.0375, 30.7420, 30.7420, \r\n
    -0.0336, 30.7397, 30.7397, \r\n
    ...

The POR line's second field is an Excel/Lotus serial date (days since
1899-12-30) -- NOT a Unix timestamp. Data rows are comma-separated
pressure, temp1, temp2 with a trailing comma/space (empty 4th field).
Encoding is ISO-8859-1 (matches the rest of this app's gauge file
handling).
"""
import datetime as _dt
import io

EXCEL_EPOCH = _dt.datetime(1899, 12, 30)


def parse_gauge_log(file_obj):
    """Returns a dict with metadata and parallel timestamp/pressure/
    temp1/temp2 lists. Raises ValueError with a clear message if the
    file doesn't look like a Kuster gauge log at all."""
    raw = file_obj.read()
    text = raw.decode("iso-8859-1") if isinstance(raw, bytes) else raw
    lines = text.splitlines()

    metadata = {}
    data_start = None
    por_dt = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "[Data]":
            data_start = i + 1
            continue
        if data_start is None:
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                metadata[key.strip()] = val.strip()
            continue
        if stripped.upper().startswith("POR"):
            parts = [p.strip() for p in stripped.split(",")]
            if len(parts) >= 2:
                try:
                    serial = float(parts[1])
                    por_dt = EXCEL_EPOCH + _dt.timedelta(days=serial)
                except ValueError:
                    por_dt = None
            data_start = i + 1
            break

    if not metadata:
        raise ValueError("Doesn't look like a Kuster gauge log (no header metadata found)")
    if data_start is None:
        raise ValueError("No [Data] section found in this file")

    try:
        sample_rate = float(metadata.get("CurrentSampleRate", "1.0"))
    except ValueError:
        sample_rate = 1.0

    pressures, temp1s, temp2s = [], [], []
    for line in lines[data_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = [p.strip() for p in stripped.split(",") if p.strip() != ""]
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            continue
        if len(vals) >= 1:
            pressures.append(vals[0])
        if len(vals) >= 2:
            temp1s.append(vals[1])
        if len(vals) >= 3:
            temp2s.append(vals[2])

    n = len(pressures)
    if n == 0:
        raise ValueError("No readable data rows found after [Data]")
    if por_dt is None:
        por_dt = _dt.datetime.now()

    timestamps = [por_dt + _dt.timedelta(seconds=i * sample_rate) for i in range(n)]

    return {
        "metadata": metadata,
        "model_number": metadata.get("ModelNumber", "\u2014"),
        "serial_number": metadata.get("SerialNumber", "\u2014"),
        "calibration_date": metadata.get("CalibrationDate", "\u2014"),
        "sample_rate": sample_rate,
        "start_time": timestamps[0],
        "end_time": timestamps[-1],
        "duration_seconds": (timestamps[-1] - timestamps[0]).total_seconds(),
        "num_samples": n,
        "timestamps": timestamps,
        "pressure": pressures,
        "temp1": temp1s,
        "temp2": temp2s,
    }


def build_gauge_log_chart_png(parsed, title):
    """Builds a quick dual-axis Pressure/Temperature vs Time chart and
    returns raw PNG bytes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax1 = plt.subplots(figsize=(9, 4.5))
    ax1.plot(parsed["timestamps"], parsed["pressure"], color="tab:blue", linewidth=0.8)
    ax1.set_xlabel("Time")
    ax1.set_ylabel("Pressure (raw)", color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    ax2 = ax1.twinx()
    ax2.plot(parsed["timestamps"], parsed["temp1"], color="tab:red", linewidth=0.8)
    ax2.set_ylabel("Temperature (\u00b0C)", color="tab:red")
    ax2.tick_params(axis="y", labelcolor="tab:red")

    fig.suptitle(title, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def compute_station_annotations(parsed, stations):
    """Matches each station's END time (HH:MM:SS) against this gauge
    log's time range and returns the MD-mGL depth value to annotate on
    the chart at that point in time. Station end_datetime is stored
    timezone-aware (UTC); the gauge log's timestamps are naive local
    time straight from the file's POR reference, so station times are
    converted to local and stripped of tzinfo before comparing."""
    from django.utils import timezone as dj_timezone
    annotations = []
    start, end = parsed["start_time"], parsed["end_time"]
    for s in stations:
        if s.end_datetime is None or s.md_mgl is None:
            continue
        dt = s.end_datetime
        if dj_timezone.is_aware(dt):
            dt = dj_timezone.localtime(dt).replace(tzinfo=None)
        if start <= dt <= end:
            annotations.append({"time": dt.isoformat(), "md_mgl": s.md_mgl, "station_id": s.pk})
    return annotations
