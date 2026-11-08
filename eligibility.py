"""Conservative evidence extraction; ambiguous notices always require human review."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dates_tr import deadlines, application_period


def norm(text):
    return re.sub(r'\s+', ' ', text.translate(str.maketrans('İıŞşĞğÜüÖöÇç', 'IiSsGgUuOoCc')).lower()).strip()


def assess(text, config, today=None, now=None):
    today = today or date.today()
    text = norm(text)
    candidate = config['candidate']
    settings = config['search_settings']
    reasons, warnings = [], []
    result = {'status': 'review', 'reasons': reasons, 'warnings': warnings,
              'deadline': None, 'minimum_score': None,
              'opens_on': None, 'deadline_time': None, 'application_status': 'unverified'}
    # Multiple dates are ambiguous: never use the first date of a multi-posting page.
    period = application_period(text)
    dates = period['dates']
    if len(dates) == 1:
        deadline = dates.pop()
        result['deadline'] = deadline.isoformat()
        result['deadline_time'] = period['deadline_time']
        result['opens_on'] = period['opens_on'].isoformat() if period['opens_on'] else None
        result['application_status'] = 'open'
        if deadline < today:
            result['application_status'] = 'closed'
            if settings.get('only_active_applications', True):
                reasons.append('Son başvuru tarihi geçmiş')
        elif period['opens_on'] and period['opens_on'] > today:
            result['application_status'] = 'not_started'
        if now and period['deadline_time']:
            zone = ZoneInfo(config.get('schedule', {}).get('timezone', 'Europe/Istanbul'))
            closes = datetime.fromisoformat(deadline.isoformat() + 'T' + period['deadline_time']).replace(tzinfo=zone)
            if now >= closes:
                result['application_status'] = 'closed'
                if settings.get('only_active_applications', True) and 'Son başvuru tarihi geçmiş' not in reasons:
                    reasons.append('Son başvuru saati geçmiş')
    else:
        warnings.append('Son başvuru tarihi belirlenemedi veya birden fazla tarih var')
    # Only explicit score type + explicit minimum wording count as score evidence.
    scores = []
    pattern = r'(?:kpss\s*[- ]*)?p\s*(3|93|94)\b[^.;]{0,100}?(?:en az|asgari|minimum)\s*(\d{2,3}(?:[,.]\d+)?)\s*puan'
    for match in re.finditer(pattern, text):
        scores.append(('P' + match[1], float(match[2].replace(',', '.'))))
    reverse = r'(?:en az|asgari|minimum)\s*(\d{2,3}(?:[,.]\d+)?)\s*puan[^.;]{0,60}?p\s*(3|93|94)\b'
    for match in re.finditer(reverse, text):
        scores.append(('P' + match[2], float(match[1].replace(',', '.'))))
    scores = [(kind, score) for kind, score in scores if 0 <= score <= 100]
    no_kpss = bool(re.search(r'kpss\s*(?:sarti aranma(?:z|maktadir)|sarti yok|sartsiz)', text))
    types = set(re.findall(r'p\s*(3|93|94)\b', text))
    relevant = [score for kind, score in scores if kind == candidate['kpss_score_type']]
    complex_notice = len(types) > 1 or len(set(scores)) > 1 or (no_kpss and bool(scores))
    if complex_notice:
        warnings.append('Birden fazla kadro/puan koşulu var; satır bazında inceleme gerekli')
    elif relevant:
        minimum = min(relevant)
        result['minimum_score'] = minimum
        if float(candidate['kpss_score']) < minimum:
            reasons.append(f"Puan yetersiz: {candidate['kpss_score']} < {minimum:g}")
        else:
            warnings.append(f"Puan eşiği karşılanıyor: {candidate['kpss_score_type']} en az {minimum:g}")
    elif scores:
        reasons.append('Belirtilen KPSS puan türü adayın puan türünden farklı')
    elif no_kpss:
        if not settings.get('include_jobs_without_kpss', True):
            reasons.append('KPSS şartı olmayan ilanlar kapalı')
        else:
            warnings.append('Metinde KPSS şartı aranmadığı belirtiliyor')
    else:
        warnings.append('KPSS taban puanı açık biçimde okunamadı')
    if candidate.get('education_level') == 'on_lisans' and not complex_notice:
        if re.search(r'(?:en az lisans|lisans mezunu olmak|lisans mezunu olmasi)', text) and not re.search(r'on\s*lisans|3001', text):
            reasons.append('Açık lisans mezuniyeti şartı var; aday ön lisans mezunu')
    if candidate.get('score_is_approximate'):
        warnings.append('Aday puanı yaklaşık; başvuruda kesin puan kullanılmalı')
    # These are evidence snippets, never an assertion of complete eligibility.
    department = norm(candidate.get('department', ''))
    if re.search(r'\b3001\b', text):
        warnings.append('3001 kodu bulundu; ilgili kadro satırı ve diğer şartlar kontrol edilmeli')
    elif department and department in text:
        warnings.append('Bölüm adı metinde bulundu; ilgili kadro satırı kontrol edilmeli')
    else:
        warnings.append('Mezuniyet/bölüm uygunluğu doğrulanamadı')
    years = set(re.findall(r'\b(20\d{2})\s*(?:yili\s*)?[- ]?\s*kpss\b', text))
    if years and str(candidate['kpss_year']) not in years:
        warnings.append('İlanda farklı KPSS yılı var; yıl şartını kontrol et')
    if settings.get('check_special_conditions', True):
        warnings.append('Yaş, belge, deneyim ve diğer özel şartlar ayrıca kontrol edilmeli')
    if reasons:
        result['status'] = 'excluded'
    return result


ROLE = r'(?:buro personeli|destek personeli|temizlik gorevlisi|guvenlik gorevlisi|hizmetli|tekniker|teknisyen|grafiker|zabita memuru|itfaiye eri|memur|sofor)'


def table_positions(rows):
    """Require explicit column headers, not spatial guesses across wrapped PDF cells."""
    positions = []
    columns = None
    for row in rows:
        values = [norm(str(cell)) for cell in row]
        if any(re.search(r'kadro|unvan|pozisyon', v) for v in values) and any('puan' in v for v in values):
            columns = values
            continue
        if not columns or len(columns) != len(values):
            continue
        def cell(pattern):
            return next((values[i] for i, h in enumerate(columns) if re.search(pattern, h)), '')
        role = cell('kadro|unvan|pozisyon')
        kind = cell(r'puan tur|kpss tur')
        score = cell(r'taban puan|asgari puan|en az puan|minimum puan')
        match = re.fullmatch(r'(?:en az |asgari |minimum )?(\d{2,3}(?:[,.]\d+)?)(?: puan)?', score)
        if not role or not re.fullmatch(r'(?:kpss\s*)?p\s*(?:3|93|94)', kind) or not match:
            continue
        score = match[1]
        if not 0 <= float(score.replace(',', '.')) <= 100:
            continue
        degree = cell('ogrenim|mezuniyet|bolum|nitelik')
        positions.append({'title': role, 'text': f'{role}: {kind} en az {score} puan. {degree}',
                          'qualification_codes': re.findall(r'\b[345]\d{3}\b', degree), 'education': degree,
                          'evidence': 'explicit_table_columns'})
    return positions


def line_positions(text):
    positions = []
    for line in text.splitlines():
        normalized = norm(line)
        if re.search(r'p\s*(?:3|93|94)\s+(?:en az|asgari|minimum)\s*\d{2,3}(?:[,.]\d+)?$', normalized):
            line += ' puan'
            normalized += ' puan'
        # Exactly one position and one explicit condition on the SAME line.
        roles = list(re.finditer(ROLE, normalized))
        if len(roles) != 1 or not re.search(r'p\s*(?:3|93|94)\b', normalized):
            continue
        if not re.search(r'(?:en az|asgari|minimum)\s*\d{2,3}(?:[,.]\d+)?\s*puan', normalized):
            continue
        if len(re.findall(r'p\s*(?:3|93|94)\b', normalized)) != 1:
            continue
        positions.append({'title': roles[0][0], 'text': line, 'evidence': 'single_line',
                          'qualification_codes': re.findall(r'\b[345]\d{3}\b', normalized)})
    return positions


def complete_positions(text, rows, positions, incomplete):
    """Whole-notice rejection needs proof that no unread position was omitted."""
    if incomplete or not positions or re.search(r'\bdiger\b|\bekte\b|\bekindeki\b|\bdevami\b', norm(text)):
        return False
    # Extra score-bearing content is evidence that the parsed table is partial.
    if len(re.findall(r'\bp\s*(?:3|93|94)\b', norm(text))) > len(positions):
        return False
    if rows:
        # Strict flat tables only: every nonempty row after one header must parse.
        nonempty = [r for r in rows if any(str(c).strip() for c in r)]
        return (len(nonempty) == len(positions) + 1
                and len(table_positions(nonempty)) == len(positions)
                and all(len(r) == len(nonempty[0]) for r in nonempty))
    lines = [norm(line) for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not re.match(r'^(?:son basvuru|basvuru bitis)', line)]
    return len(lines) == len(positions) and all(';' not in line and len(line_positions(line)) == 1 for line in lines)


def assess_notice(text, config, today=None, rows=None, incomplete=False, now=None):
    document_incomplete = incomplete
    result = assess(text, config, today, now=now)
    global_reasons = list(result['reasons'])
    global_warnings = list(result['warnings'])
    parsed_table = table_positions(rows or [])
    table_like = sum(len(row) >= 3 for row in (rows or [])) >= 2
    if table_like and not parsed_table:
        incomplete = True
    positions = parsed_table or line_positions(text)
    evidence = []
    for position in positions:
        assessment = assess(position['text'], config, today, now=now)
        # Never inherit a deadline from a different position.
        position = dict(position, assessment=assessment)
        evidence.append(position)
    result['positions'] = evidence
    result['positions_complete'] = complete_positions(text, rows, positions, incomplete)
    if evidence:
        # A single failed position may never exclude an entire mixed announcement.
        result['status'] = 'review'
        result['reasons'] = []
        result['minimum_score'] = None
        result['warnings'] = list(dict.fromkeys(['Kadro koşulları ayrı değerlendirildi; genel/özel şartlar ayrıca kontrol edilmeli'] + global_warnings))
        dates = deadlines(norm(text))
        result['deadline'] = next(iter(dates)).isoformat() if len(dates) == 1 else None
        # A global date is only safe when each row has no local date or all match it.
        row_dates = [deadlines(norm(p['text'])) for p in evidence]
        if len(evidence) > 1 and any(row_dates):
            result['deadline'] = None
            result['deadline_time'] = None
            result['opens_on'] = None
            result['application_status'] = 'unverified'
        if result['positions_complete'] and all(p['assessment']['status'] == 'excluded' for p in evidence):
            result['status'] = 'excluded'
            result['reasons'] = ['Ayrıştırılan kadroların tamamı için açık bir koşul karşılanmıyor']
        if result['deadline'] and any('Son başvuru' in reason for reason in global_reasons):
            result['status'] = 'excluded'
            result['reasons'] = [r for r in global_reasons if 'Son başvuru' in r]
    # Flat/partial tables are insufficient for whole-announcement rejection.
    roles = re.findall(ROLE, norm(text))
    if (len(set(roles)) > 1 and not evidence) or incomplete:
        result['status'] = 'review'
        result['reasons'] = []
        result['observed_deadline'] = result['deadline']
        # A fully read, explicit application period is independent of an
        # ambiguous qualifications table. Partial attachments remain uncertain.
        if document_incomplete or not application_period(text)['explicit_application_range']:
            result['deadline'] = None
            result['deadline_time'] = None
            result['opens_on'] = None
            result['application_status'] = 'unverified'
        elif result['application_status'] == 'closed' and config['search_settings'].get('only_active_applications', True):
            result['status'] = 'excluded'
            result['reasons'] = ['Doğrulanan başvuru süresi geçmiş; kadro şartları ayrıca belirsiz']
        result['warnings'].append('Belge/kadro kapsamı belirsiz; kadro şartlarından kesin eleme yapılmadı')
    return result
