"""Resolve a single explicit issuer PDF link on an ordinary HTTP-200 page."""
from urllib.parse import urljoin, urlsplit, unquote
from bs4 import BeautifulSoup
from ..core.access_policy import is_blocked_terms_target


def issuer_pdf_link(content, *, page_url, artifact_type, blocked_hosts=()):
    if artifact_type not in ('transcript','presentation'):
        return None
    soup=BeautifulSoup(content,'html.parser')
    if soup.select_one('input[type="password"],iframe[src*="captcha"],.g-recaptcha'):
        return None
    article=soup.select_one('article .entry-content,article,main,.entry-content')
    if article is None:
        return None
    expected=('transcript','concall','conference-call','earnings-call') if artifact_type=='transcript' else ('presentation','investor','fact-sheet','factsheet')
    host=(urlsplit(page_url).hostname or '').removeprefix('www.').lower()
    candidates=set()
    for anchor in article.select('a[href]'):
        url=urljoin(page_url,anchor['href']); parsed=urlsplit(url)
        try:
            port = parsed.port
        except ValueError:
            continue
        if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.fragment
            or port not in (None,443) or not parsed.path.lower().endswith('.pdf')
            or (parsed.hostname or '').removeprefix('www.').lower()!=host
            or is_blocked_terms_target(url,additional_hosts=blocked_hosts)):
            continue
        label=unquote(parsed.path).lower()+' '+anchor.get_text(' ',strip=True).lower()
        if any(word in label for word in expected):
            candidates.add(url)
    return next(iter(candidates)) if len(candidates)==1 else None
