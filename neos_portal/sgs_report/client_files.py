"""The data files that go to the client with an SGS report.

Kuster's software exports each gauge as a .txt: a header describing the gauge *and the job*, then the
`dd/mm/yyyy  Time  Pressure  Temperature` table. Before it is sent to the client two things change:

  * the header is rebuilt: the gauge details from the uploaded file, then Client / Well NO / Type of
    survey / Field / Date of Survey from the SGS report, then the units (the number-only serial, 8698);
  * the file is renamed:  <well>_SGS Data_<library serial, D8698>_<Bot|Top>_<yyyymmdd>.txt

The data rows of a .txt export are copied exactly as exported (same bytes, same line endings), never
re-written. A CSV export is laid out as the same fixed-width table first.
"""
import datetime
import re
from decimal import ROUND_HALF_UP, Decimal

TABLE_HEADER = "dd/mm/yyyy     Time           Pressure       Temperature    "
TABLE_START = re.compile(r"(?m)^dd/mm/yyyy")
# lines kept from the export's header, in the order the client wants them
KEPT = ["Gauge Model Number", "Gauge Manufacturer", "Maximum Recorder Range", "Date of Last Calibration"]
UNITS = ["Pressure Units", "Temperature Units"]
POSITION = {"bottom": "Bot", "top": "Top"}


def read_header(raw):
    """(header fields, text before the table, the table onward, line ending) from an exported file."""
    text = raw.decode("latin-1")
    start = TABLE_START.search(text)
    if not start:
        raise ValueError("This isn't a Kuster export: no 'dd/mm/yyyy  Time  Pressure  Temperature' table was found.")
    head, table = text[:start.start()], text[start.start():]
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep and "*****" not in line:
            fields[key.strip()] = value.strip()
    newline = "\r\n" if "\r\n" in text else "\n"
    return fields, head, table, newline


def column_positions(header_row):
    """Where Pressure and Temperature sit in a comma-separated table, read from its header row.

    Kuster's CSV comes in two layouts -- "Date/Time, Delta(Hours), Pressure, Temperature, ..." and the
    shorter "dd/mm/yyyy  Time,Pressure,Temperature," -- so the position is taken from the column names
    (falling back to the first layout when the names can't be found)."""
    names = [c.strip().lower() for c in header_row.split(",")]
    pressure = next((i for i, n in enumerate(names) if n.startswith("pressure")), None)
    temperature = next((i for i, n in enumerate(names) if n.startswith("temperature")), None)
    return (pressure, temperature) if pressure is not None and temperature is not None else (2, 3)


def csv_to_table(table, newline):
    """A Kuster CSV export ("dd/mm/yyyy hh:mm:ss, delta, pressure, temperature, ...") laid out as the
    client's fixed-width table. Pressure and temperature keep the 3 decimals Kuster gives (rounded half up
    from the text, never through a float)."""
    out, skipped = [TABLE_HEADER], 0
    rows = table.splitlines()
    p_at, t_at = column_positions(rows[0])
    for line in rows[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            date_part, time_part = parts[0].split()
            datetime.datetime.strptime(date_part, "%d/%m/%Y")
            stamp = time_part.split(".")[0]
            datetime.datetime.strptime(stamp, "%H:%M:%S")
            pressure = Decimal(parts[p_at]).quantize(Decimal("0.001"), ROUND_HALF_UP)
            temperature = Decimal(parts[t_at]).quantize(Decimal("0.001"), ROUND_HALF_UP)
        except (ValueError, IndexError, ArithmeticError):
            skipped += 1
            continue
        out.append(f"{date_part:<15}{stamp:<15}{pressure:<15}{temperature:<15}")
    if len(out) == 1:
        raise ValueError("No readable data rows were found in this CSV.")
    return newline.join(out) + newline, skipped


def digits(serial):
    return re.sub(r"\D", "", serial or "")


def resolve_serial(file_serial, report, side):
    """The gauge's serial as the library has it (D8699), and a warning if it had to guess."""
    from .models import Gauge

    wanted = digits(file_serial)
    if wanted:
        for gauge in Gauge.objects.all():
            if digits(gauge.serial_number) == wanted:
                return gauge.serial_number, ""
    chosen = getattr(report, f"{side}_gauge", None)
    if chosen:
        note = "" if not wanted or digits(chosen.serial_number) == wanted else (
            f"The file says serial {file_serial}, but this report's {side} gauge is {chosen.serial_number}.")
        return chosen.serial_number, note
    return file_serial, f"Serial {file_serial} isn't in the Gauge Library, so it is used as written."


def survey_date(fields, table, report):
    """yyyymmdd: the report's date of test, else the export's Date of Survey, else the first reading."""
    if report.date_of_test:
        return report.date_of_test.strftime("%Y%m%d")
    given = fields.get("Date of Survey", "")
    if re.fullmatch(r"\d{8}", given):
        return given
    first = re.search(r"(?m)^(\d\d)/(\d\d)/(\d{4})", table)
    return f"{first.group(3)}{first.group(2)}{first.group(1)}" if first else datetime.date.today().strftime("%Y%m%d")


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|]+', "-", text).strip()


def field_line(key, value):
    """One header line the way Kuster's own export writes it: the name padded to 33 characters, then ': value'."""
    return f"{key:<33}: {value}"


def plain_serial(serial):
    """8698 for the library's D8698 -- the client's header carries the number only."""
    return re.sub(r"^[A-Za-z]+", "", serial or "").strip()


def prepare(report, side, raw):
    """The client's version of one gauge file: {"name", "content" (bytes), "warnings", "serial"}.

    Header as the client receives it: gauge details (from the uploaded file), then Client / Well NO / Type of
    survey / Field / Date of Survey (from this report), then the units. The file name carries the library's
    serial (D8698); the header carries the number alone (8698)."""
    fields, _head, table, newline = read_header(raw)
    missing = [k for k in KEPT + UNITS if k not in fields]
    if missing:
        raise ValueError("The export's header is missing: " + ", ".join(missing))
    serial, note = resolve_serial(fields.get("Gauge Serial Number", ""), report, side)

    warnings = [note] if note else []
    converted = "," in table.splitlines()[0]
    if converted:
        table, skipped = csv_to_table(table, newline)
        if skipped:
            warnings.append(f"{skipped} line(s) of the CSV could not be read and were left out.")

    # The job lines come from the report; if the report lacks one, the file's own line is used.
    client = (report.client or report.company or fields.get("Client", "")).strip()
    well = (report.well_name or fields.get("Well NO", "")).strip()
    survey = (report.report_type or fields.get("Type of survey", "")).strip()
    field = (report.field or fields.get("Field", "")).strip()
    date = survey_date(fields, table, report)
    for label, value in (("Client", client), ("Field", field)):
        if not value:
            warnings.append(f"{label} is empty on this report, so that line of the header is blank.")
    file_well = fields.get("Well NO", "")
    if file_well and well and file_well.strip().lower() != well.lower():
        warnings.append(f"The file says well {file_well}, but this report is for {well}.")
    file_date = fields.get("Date of Survey", "")
    if re.fullmatch(r"\d{8}", file_date) and file_date != date:
        warnings.append(f"The file says the survey was on {file_date}, but this report's date is {date}.")

    lines = [
        field_line("Gauge Serial Number", plain_serial(serial)),
        field_line("Gauge Model Number", fields["Gauge Model Number"]),
        field_line("Gauge Manufacturer", fields["Gauge Manufacturer"]),
        field_line("Maximum Recorder Range", fields["Maximum Recorder Range"]),
        field_line("Date of Last Calibration", fields["Date of Last Calibration"]),
        field_line("Client", client),
        field_line("Well NO", well),
        field_line("Type of survey", survey),
        field_line("Field", field),
        field_line("Date of Survey", date),
        field_line("Pressure Units", fields["Pressure Units"]),
        field_line("Temperature Units", fields["Temperature Units"]),
        "",
    ]
    content = (newline.join(lines) + newline + table).encode("latin-1")
    name = f"{safe_name(well)}_SGS Data_{serial}_{POSITION[side]}_{date}.txt"
    return {"name": name, "content": content, "warnings": warnings, "serial": serial, "converted": converted}
