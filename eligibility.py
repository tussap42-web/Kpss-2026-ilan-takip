"""Conservative evidence extraction; ambiguous notices always require human review."""
import re
from datetime import date


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
    dates = set()
    for match in re.finditer(r'(?:son basvuru(?: tarihi)?|basvuru bitis(?: tarihi)?)\s*[:\-]?\s*(\d{1,2})[./-](\d{1,2})[./-](20\d{2})', text):
        try:
            dates.add(date(int(match[3]), int(match[2]), int(match[1])))
        except ValueError:
            pass
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
