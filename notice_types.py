"""Classify the notice itself, never incidental links to future exam results."""
import re
from urllib.parse import urlsplit
from eligibility import norm


def notice_kind(title, url='', document_title=''):
    headings = norm(title + ' ' + document_title)
    path = norm(urlsplit(url).path.replace('-', ' ').replace('_', ' '))
    evidence = headings + ' ' + path
    rules = (
        ('cancelled', r'\biptal\b'),
        ('correction', r'\bduzeltme\b|\bsure uzatim|\bsure uzatil'),
        ('regulation', r'\bgenelge\b|\byonerge\b|\byonetmelik\b|\busul ve esaslar\b'),
        ('reserve_call', r'\byedek\b'),
        ('results', r'\bsonuc(?:u|lar\w*)?\b|\byerlestirilen aday'),
        ('exam_notice', r'\bsinav (?:yeri|yerleri|tarihi|tarihleri|girisi|giris)\b|\bsinav yeri ve tarihler'),
        ('information', r'\bozel istihdam burolari\b|\bbasvuru bilgileri\b'),
    )
    for kind, pattern in rules:
        if re.search(pattern, evidence):
            return kind
    return 'recruitment' if re.search(
        r'personel|memur|isci|hizmetli|itfaiye eri|zabita|kpss', evidence
    ) else 'unknown'


NON_RECRUITMENT = {'cancelled', 'correction', 'regulation', 'reserve_call',
                   'results', 'exam_notice', 'information'}


def needs_date_verification(url, assessment):
    # News without a full application date is quarantined; its year is not guessed.
    return ('/haber/' in urlsplit(url).path.lower()
            and not assessment.get('deadline'))
