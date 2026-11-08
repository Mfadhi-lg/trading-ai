"""Existing browser rating report and issuer checks, including OCR evidence."""
import io
import re
from bs4 import BeautifulSoup
from pypdf import PdfReader
from ...credit_rating_resolver import is_genuine_html_credit_report
from ...rating_identity import match_company
from .errors import PublisherFileUnavailable

def is_india_rating_commentary(body, final_url):
    """NRAC updates need not contain a facilities table or rationale heading."""
    if not re.match(r'https://www\.indiaratings\.co\.in/pressrelease/\d+/?$', final_url):
        return False
    soup = BeautifulSoup(body, 'html.parser')
    for element in soup(['script', 'style', 'nav', 'footer']):
        element.decompose()
    text = soup.get_text(' ', strip=True)
    return (len(text) >= 1200 and 'India Ratings and Research' in text
            and bool(re.search(r'\brat(?:ing|ings|ed)\b', text, re.I))
            and bool(re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}', text))
            and not re.search(r'captcha|access denied|you have been blocked', text, re.I))


def validate_report(body, media_type, final_url, company_name, aliases=(), *, PdfReader=PdfReader,
                    is_genuine_html_credit_report=is_genuine_html_credit_report,
                    is_india_rating_commentary=is_india_rating_commentary,
                    match_company=match_company):
    if media_type == 'application/pdf':
        if not body.startswith(b'%PDF-'):
            raise ValueError('Not PDF bytes')
        reader = PdfReader(io.BytesIO(body), strict=False)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
            raise ValueError('Encrypted or implausible PDF page count')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages[:5])
        evidence = {'page_count': len(reader.pages), 'text_length': len(text)}
        if len(text.strip()) < 100 and re.search(r'\bno file to view\b', text, re.I):
            raise PublisherFileUnavailable('PUBLISHER_SOURCE_UNAVAILABLE: PDF says NO FILE TO VIEW')
        if len(text.strip()) < 100 or not re.search(r'\brat(?:ing|ings|ed)\b', text, re.I):
            # Preserve the original PDF; OCR is validation evidence, not a replacement file.
            import pdfplumber
            import pytesseract
            ocr_pages = []
            with pdfplumber.open(io.BytesIO(body)) as pdf:
                for page in pdf.pages[:5]:
                    ocr_pages.append(pytesseract.image_to_string(
                        page.to_image(resolution=150).original, lang='eng',
                        config='--psm 6', timeout=60))
            text = '\n'.join(ocr_pages)
            evidence.update(validation_text_origin='ocr', ocr_page_count=len(ocr_pages),
                            text_length=len(text), ocr_engine='tesseract')
            if len(text.strip()) < 100 or not re.search(r'\brat(?:ing|ings|ed)\b', text, re.I):
                raise ValueError('PDF has no usable rating report text after OCR')
    elif media_type == 'text/html':
        genuine, evidence = is_genuine_html_credit_report(body, final_url=final_url)
        if not genuine:
            missing_text = BeautifulSoup(body, 'html.parser').get_text(' ', strip=True)
            if len(missing_text) < 200 and re.search(r'\bfile not found\b', missing_text, re.I):
                raise PublisherFileUnavailable('PUBLISHER_SOURCE_UNAVAILABLE: HTML says File not found')
        if not genuine and not is_india_rating_commentary(body, final_url):
            raise ValueError(f'Not a genuine HTML rating report: {evidence}')
        evidence['india_rating_commentary'] = not genuine
        soup = BeautifulSoup(body, 'html.parser')
        for element in soup(['script', 'style', 'nav', 'footer']):
            element.decompose()
        text = soup.get_text(' ', strip=True)
    else:
        raise ValueError('Unsupported media type')
    match = match_company(text, company_name, aliases)
    evidence['company_name_match'] = bool(match)
    if match:
        evidence['company_identity_match'] = match
    if not match:
        raise ValueError(f'REVIEW_REQUIRED: company name not found: {company_name}')
    return evidence


