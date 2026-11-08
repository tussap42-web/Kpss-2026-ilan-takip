"""Explicit application periods only; never borrow a year from publication dates."""
import re
from datetime import date

MONTHS = {name: i + 1 for i, name in enumerate('ocak subat mart nisan mayis haziran temmuz agustos eylul ekim kasim aralik'.split())}
MONTH = '|'.join(MONTHS)
NUMERIC = r'(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(20\d{2})'
LABEL = r'(?:son\s+(?:basvuru|muracaat)(?:\s+tarihi)?|basvuru\s+bitis(?:\s+tarihi)?)\s*[:\-]?\s*'


def application_period(text):
    text = re.sub(r'\s+', ' ', text.translate(str.maketrans('İıŞşĞğÜüÖöÇç', 'IiSsGgUuOoCc')).lower())
    found, starts, times, range_ends = set(), set(), set(), set()

    def parsed(day, month, year):
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None

    def ending(day, month, year, tail=''):
        end = parsed(day, month, year)
        if end:
            found.add(end)
            m = re.match(r"(?:\s+tarihinde)?\s*(?:saat\s*)?(\d{1,2})[:.](\d{2})(?:\b|['’])", tail)
            if m and int(m[1]) < 24 and int(m[2]) < 60:
                times.add((end, f'{int(m[1]):02d}:{int(m[2]):02d}'))

    for m in re.finditer(LABEL + NUMERIC, text):
        ending(*m.groups(), text[m.end():m.end()+45])
    for m in re.finditer(LABEL + rf'(\d{{1,2}})\s+({MONTH})\s+(20\d{{2}})', text):
        ending(m[1], MONTHS[m[2]], m[3], text[m.end():m.end()+45])
    for m in re.finditer(rf'basvurular\s+(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})\s+({MONTH})\s+(20\d{{2}})\s+tarihleri\s+arasinda', text):
        start, end = parsed(m[1], MONTHS[m[3]], m[4]), parsed(m[2], MONTHS[m[3]], m[4])
        if start and end and start <= end:
            starts.add(start)
            found.add(end)
            range_ends.add(end)
    # Also accepts spaced date cells from official PDF layout extraction.
    for m in re.finditer(NUMERIC + r'\s*[-–]\s*' + NUMERIC + r'\s+tarihleri\s+arasinda', text):
        context = text[max(0,m.start()-240):m.start()]
        if not re.search(r'basvur|muracaat', context):
            continue
        purpose = list(re.finditer(r'basvur\w*|muracaat\w*|sinav\w*|mulakat\w*', context))
        if purpose and not re.match(r'basvur|muracaat', purpose[-1][0]):
            # An application section may say "to participate in the exam";
            # this is different from a range explicitly introduced as exam dates.
            application_purpose = re.search(r'sinava katilabilmek icin\s*[,;:]?\s*$', context)
            if not application_purpose:
                continue
        start, end = parsed(*m.groups()[:3]), parsed(*m.groups()[3:])
        if start and end and start <= end:
            starts.add(start)
            found.add(end)
            range_ends.add(end)
    # Explicit "applications start ... and end ..." (e.g. the SBB notice).
    pattern = r'basvurular\s+' + NUMERIC + r'\s+tarihinde\s+baslayacak\s+ve\s+' + NUMERIC
    for m in re.finditer(pattern, text):
        tail = text[m.end():m.end()+90]
        if not re.match(r"\s+tarihinde\s+(?:saat\s+\d{1,2}[:.]\d{2}['’]?\s*(?:da|de|ta|te)?\s+)?sona erecektir", tail):
            continue
        start, end = parsed(*m.groups()[:3]), parsed(*m.groups()[3:])
        if start and end and start <= end:
            starts.add(start)
            ending(*m.groups()[3:], tail)
            range_ends.add(end)
    deadline = next(iter(found)) if len(found) == 1 else None
    start = next(iter(starts)) if len(starts) == 1 and deadline else None
    clock = {t for d,t in times if d == deadline}
    return {'dates': found, 'deadline': deadline,
            'opens_on': start, 'deadline_time': next(iter(clock)) if len(clock) == 1 else None,
            'explicit_application_range': deadline is not None and range_ends == {deadline}}


def deadlines(text):
    return application_period(text)['dates']
