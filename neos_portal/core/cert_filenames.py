"""Reading an expiry date out of a certificate's file name.

The bulk uploaders take the date from the file name. Names come in several shapes, so one reader is
shared by both (equipment and personnel):

    03082026_...            ddmmyyyy
    EXP 20260803_...        an "EXP" marker in front (space, dash, dot or nothing after it), date as yyyymmdd
    EXP_20260803_...        the marker as a part of its own
    2026-08-03 / 03-08-2026 / 03.08.2026   separated dates

When all eight digits could be either order the marker decides: with EXP it is yyyymmdd (how those
files are named), without it ddmmyyyy (how the older files are named).
"""
import datetime
import re

MARKER = re.compile(r"^\s*exp(?:iry|ires|ired|iration)?(?![a-z])\.?[\s:\-_.]*", re.IGNORECASE)
MARKER_ONLY = re.compile(r"^\s*exp(?:iry|ires|ired|iration)?\.?\s*$", re.IGNORECASE)
DATE = re.compile(r"^\s*(\d{8}|\d{1,4}[-./]\d{1,2}[-./]\d{1,4})(?!\d)")


def _make(year, month, day):
    try:
        made = datetime.date(year, month, day)
    except ValueError:
        return None
    return made if 2000 <= made.year <= 2100 else None


def read_date(part):
    """The date at the start of `part` (after an optional EXP marker), or None."""
    marked = bool(MARKER.match(part))
    rest = MARKER.sub("", part, count=1) if marked else part
    found = DATE.match(rest)
    if not found:
        return None
    text = found.group(1)
    if text.isdigit():
        ymd = _make(int(text[:4]), int(text[4:6]), int(text[6:]))
        dmy = _make(int(text[4:]), int(text[2:4]), int(text[:2]))
        if ymd and dmy:
            return ymd if marked else dmy
        return ymd or dmy
    a, b, c = (int(x) for x in re.split(r"[-./]", text))
    if len(re.split(r"[-./]", text)[0]) == 4:
        return _make(a, b, c)
    return _make(c, b, a)


def drop_marker_parts(parts):
    """File-name parts without a stand-alone EXP part (EXP_20260803_...)."""
    return [p for p in parts if not MARKER_ONLY.match(p)]


def clean_serial(text):
    """A serial without the " (1)" a browser adds to a repeated download."""
    return re.sub(r"\s*\(\d+\)\s*$", "", text).strip()
