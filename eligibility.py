"""Conservative evidence extraction; ambiguous notices always require human review."""
import re
from datetime import date
from dates_tr import deadlines


def norm(text):
    return re.sub(r'\s+', ' ', text.translate(str.maketrans('İıŞşĞğÜüÖöÇç', 'IiSsGgUuOoCc')).lower()).strip()


def assess(text, config, today=None):
    today = today or date.today()
    text = norm(text)
    candidate = config['candidate']
    settings = config['search_settings']
    reasons, warnings = [], []
    result = {'status': 'review', 'reasons': reasons, 'warnings': warnings,
              'deadline': None, 'minimum_score': None}
    # Multiple dates are ambiguous: never use the first date of a multi-posting page.
    dates = deadlines(text)
    if len(dates) == 1:
        deadline = dates.pop()
        result['deadline'] = deadline.isoformat()
        if deadline < today and settings.get('only_active_applications', True):
            reasons.append('Son başvuru tarihi geçmiş')
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
    # Public notice requirements must stay separate from the saved candidate's
    # score. Keep every explicit condition, including other score types.
    education = []
    if re.search(r'\b(?:ortaogretim|orta ogretim|lise)\b', text):
        education.append('Ortaöğretim / lise')
    if re.search(r'\bon lisans\b', text):
        education.append('Ön lisans')
    if re.search(r'\blisans\b', re.sub(r'\bon lisans\b', '', text)):
        education.append('Lisans')
    result.update(score_conditions=[{'score_type': kind, 'minimum_score': score}
                                    for kind, score in sorted(set(scores))],
                  score_types=sorted('P' + kind for kind in types),
                  score_ambiguous=complex_notice,
                  kpss_exempt=no_kpss and not scores and not types,
                  education_levels=education, evidence_complete=True)
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


ROLE = r'(?:buro personeli|destek personeli|temizlik gorevlisi|guvenlik gorevlisi|hizmetli|tekniker|teknisyen|grafiker|memur|sofor|zabita memuru|itfaiye eri)'


def table_positions(rows):
    """Require explicit column headers, not spatial guesses across wrapped PDF cells."""
    positions = []
    columns = None
    for row in rows:
        display_values = [' '.join(str(cell).split()) for cell in row]
        values = [norm(str(cell)) for cell in row]
        if any(re.search(r'kadro|unvan|pozisyon', v) for v in values) and any('puan' in v for v in values):
            columns = values
            continue
        if not columns or len(columns) != len(values):
            continue
        def cell(pattern, display=False):
            source = display_values if display else values
            return next((source[i] for i, h in enumerate(columns) if re.search(pattern, h)), '')
        role = cell('kadro|unvan|pozisyon')
        kind = cell(r'puan tur|kpss tur')
        score = cell(r'taban puan|asgari puan|en az puan|minimum puan')
        if not role or not re.fullmatch(r'(?:kpss\s*)?p\s*(?:3|93|94)', kind) or not re.fullmatch(r'\d{2,3}(?:[,.]\d+)?', score):
            continue
        if not 0 <= float(score.replace(',', '.')) <= 100:
            continue
        degree = cell('ogrenim|mezuniyet|bolum|nitelik')
        positions.append({'title': role, 'text': f'{role}: {kind} en az {score} puan. {degree}',
                          'display_title': cell('kadro|unvan|pozisyon', display=True),
                          'display_education': cell('ogrenim|mezuniyet|bolum|nitelik', display=True),
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


def assess_notice(text, config, today=None, rows=None, incomplete=False):
    result = assess(text, config, today)
    parsed_table = table_positions(rows or [])
    table_like = sum(len(row) >= 3 for row in (rows or [])) >= 2
    if table_like and not parsed_table:
        incomplete = True
    positions = parsed_table or line_positions(text)
    evidence = []
    for position in positions:
        assessment = assess(position['text'], config, today)
        # Never inherit a deadline from a different position.
        position = dict(position, assessment=assessment)
        evidence.append(position)
    result['positions'] = evidence
    if evidence:
        # A single failed position may never exclude an entire mixed announcement.
        result['status'] = 'review'
        result['reasons'] = []
        result['minimum_score'] = None
        result['warnings'] = ['Kadro koşulları ayrı değerlendirildi; genel/özel şartlar ayrıca kontrol edilmeli']
        dates = deadlines(norm(text))
        result['deadline'] = next(iter(dates)).isoformat() if len(dates) == 1 else None
        # A global date is only safe when each row has no local date or all match it.
        row_dates = [deadlines(norm(p['text'])) for p in evidence]
        if len(evidence) > 1 and any(row_dates):
            result['deadline'] = None
        if result['deadline'] and date.fromisoformat(result['deadline']) < (today or date.today()) and config['search_settings'].get('only_active_applications', True):
            result['status'] = 'excluded'
            result['reasons'] = ['Son başvuru tarihi geçmiş']
    # Flat/partial tables are insufficient for whole-announcement rejection.
    roles = re.findall(ROLE, norm(text))
    if (len(set(roles)) > 1 and not evidence) or incomplete:
        result['status'] = 'review'
        result['reasons'] = []
        result['deadline'] = None
        result['warnings'].append('Belge/kadro kapsamı belirsiz; kesin eleme yapılmadı')
    result['evidence_complete'] = not incomplete
    for position in evidence:
        position['assessment']['evidence_complete'] = not incomplete
    return result
