"""Only explicit application deadlines/ranges; no date guesses from news timestamps."""
import re
from datetime import date

MONTHS = {name: i + 1 for i, name in enumerate('ocak subat mart nisan mayis haziran temmuz agustos eylul ekim kasim aralik'.split())}
MONTH = '|'.join(MONTHS)
LABEL = r'(?:son\s+(?:basvuru|muracaat)(?:\s+tarihi)?|basvuru\s+bitis(?:\s+tarihi)?)\s*[:\-]?\s*'


def deadlines(text):
    found = set()
    def add(day, month, year):
        try:
            found.add(date(int(year), int(month), int(day)))
        except ValueError:
            pass
    for m in re.finditer(LABEL + r'(\d{1,2})[./-](\d{1,2})[./-](20\d{2})', text):
        add(*m.groups())
    for m in re.finditer(LABEL + rf'(\d{{1,2}})\s+({MONTH})\s+(20\d{{2}})', text):
        add(m[1], MONTHS[m[2]], m[3])
    for m in re.finditer(rf'basvurular\s+(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})\s+({MONTH})\s+(20\d{{2}})\s+tarihleri\s+arasinda', text):
        if int(m[1]) <= int(m[2]):
            add(m[2], MONTHS[m[3]], m[4])
    for m in re.finditer(r'basvurular\s+(\d{1,2})[./](\d{1,2})[./](20\d{2})\s*[-–]\s*(\d{1,2})[./](\d{1,2})[./](20\d{2})\s+tarihleri\s+arasinda', text):
        try:
            if date(int(m[3]), int(m[2]), int(m[1])) <= date(int(m[6]), int(m[5]), int(m[4])):
                add(m[4], m[5], m[6])
        except ValueError:
            pass
    return found
