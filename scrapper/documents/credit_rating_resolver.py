"""Bounded resolution of direct, landing-page, form, and HTML credit reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ..core.access_policy import (
    BlockedTermsError,
    PUBLISHER_TERMS_ERROR,
    is_blocked_terms_target,
)
from .downloader import PDF, DownloadPolicy, DownloadResult, detect_media_type, download_url


RESOLVER_VERSION = "1.0.0"
AGENCY_HOST_SUFFIXES = (
    "crisil.com", "crisilratings.com", "careratings.com", "icra.in",
    "indiaratings.co.in",
    "infomerics.com", "acuite.in", "bcrisp.in", "brickworkratings.com",
    "amazonaws.com", "cloudfront.net",
)
LINK_ATTRIBUTES = ("href", "data-href", "data-url", "data-download", "data-file")
BAD_LINK_WORDS = (
    "login", "logout", "privacy", "career", "contact", "facebook", "linkedin",
    "annexure", "methodology", "criteria", "disclaimer", "definition",
)
REPORT_WORDS = ("rating", "rationale", "press-release", "press_release", "download", "report", ".pdf")


@dataclass(frozen=True)
class ResolutionStep:
    step_type: str
    method: str
    request_url: str
    result: DownloadResult
    selected: bool
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class CreditRatingResolution:
    status: str
    original_url: str
    final_result: DownloadResult | None
    steps: tuple[ResolutionStep, ...]
    error: str | None = None


@dataclass(frozen=True)
class _Candidate:
    url: str
    method: str
    data: Mapping[str, str]
    step_type: str
    score: int


def _route_request_with_policy(route: Any) -> None:
    """Abort blocked publisher hosts before Playwright dispatches the request."""

    if is_blocked_terms_target(str(route.request.url)):
        route.abort("blockedbyclient")
        return
    route.continue_()


def _browser_resolution(
    original_url: str,
    *,
    policy: DownloadPolicy,
) -> tuple[DownloadResult | None, dict[str, Any]]:
    """Render/click a known report page when plain HTTP exposes only an app shell."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, {"browser": "unavailable"}

    captured: list[tuple[str, str, bytes]] = []
    evidence: dict[str, Any] = {"clicked_labels": [], "captured_responses": []}
    timeout_ms = int(min(policy.total_timeout_seconds, 90.0) * 1000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            # Service workers can otherwise consume requests before routing;
            # disabling them makes the host policy route authoritative.
            service_workers="block",
        )
        context.route("**/*", _route_request_with_policy)
        page = context.new_page()

        def capture(response: Any) -> None:
            if is_blocked_terms_target(str(response.url)):
                return
            content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].lower()
            if content_type == PDF or response.url.lower().split("?", 1)[0].endswith(".pdf"):
                try:
                    body = response.body()
                except Exception:
                    return
                if body and len(body) <= policy.max_bytes and detect_media_type(body, content_type) == PDF:
                    captured.append((response.url, content_type or PDF, body))
                    evidence["captured_responses"].append(response.url)

        page.on("response", capture)
        try:
            navigation = page.goto(original_url, wait_until="networkidle", timeout=timeout_ms)
        except (PlaywrightTimeout, PlaywrightError):
            navigation = None
        page.wait_for_timeout(2000)

        if not captured:
            controls = page.locator("a,button,input[type=button],input[type=submit]")
            for index in range(min(controls.count(), 30)):
                control = controls.nth(index)
                try:
                    label = " ".join(
                        filter(
                            None,
                            [
                                control.inner_text(timeout=500),
                                control.get_attribute("value"),
                                control.get_attribute("title"),
                                control.get_attribute("aria-label"),
                            ],
                        )
                    ).strip()
                except Exception:
                    continue
                if not re.search(
                    r"(?:download|view\s+(?:report|rationale)|rationale|report|pdf)",
                    label,
                    re.I,
                ):
                    continue
                evidence["clicked_labels"].append(label[:200])
                try:
                    with page.expect_download(timeout=5000) as download_info:
                        control.click(timeout=3000)
                    download = download_info.value
                    if is_blocked_terms_target(str(download.url)):
                        continue
                    path = download.path()
                    if path:
                        body = path.read_bytes()
                        detected = detect_media_type(body, PDF)
                        if detected == PDF and len(body) <= policy.max_bytes:
                            captured.append((download.url, PDF, body))
                            break
                except PlaywrightTimeout:
                    page.wait_for_timeout(1500)
                    if captured:
                        break
                except Exception:
                    continue

        if captured:
            final_url, declared, body = captured[0]
            result = DownloadResult(
                requested_url=original_url,
                final_url=final_url,
                http_status=200,
                response_headers={"content-type": declared},
                redirects=(),
                declared_media_type=declared,
                detected_media_type=PDF,
                body=body,
                byte_size=len(body),
                result_status="validated",
                elapsed_ms=0,
            )
            browser.close()
            return result, evidence

        rendered = page.content().encode("utf-8")
        final_url = page.url
        if is_blocked_terms_target(final_url):
            browser.close()
            return None, {**evidence, "browser": "blocked_terms"}
        genuine, html_evidence = is_genuine_html_credit_report(rendered, final_url=final_url)
        evidence.update(html_evidence)
        evidence["rendered_html_bytes"] = len(rendered)
        status = navigation.status if navigation is not None else 200
        browser.close()
        if genuine:
            return DownloadResult(
                requested_url=original_url,
                final_url=final_url,
                http_status=int(status),
                response_headers={"content-type": "text/html"},
                redirects=(),
                declared_media_type="text/html",
                detected_media_type="text/html",
                body=rendered,
                byte_size=len(rendered),
                result_status="validated",
                elapsed_ms=0,
            ), evidence
    return None, evidence


def _allowed_target(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in AGENCY_HOST_SUFFIXES)


def _html_text(body: bytes) -> str:
    soup = BeautifulSoup(body, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "footer"]):
        node.decompose()
    return " ".join(soup.stripped_strings)


def is_genuine_html_credit_report(body: bytes, *, final_url: str) -> tuple[bool, dict[str, Any]]:
    """Fail closed: accept only substantial, report-shaped agency HTML."""

    if not _allowed_target(final_url):
        return False, {"reason": "unapproved_host"}
    text = _html_text(body)
    lowered = text.lower()
    groups = {
        "rating": bool(re.search(r"\b(?:credit\s+rating|rating\s+action|rated|rating)\b", lowered)),
        "facility": bool(re.search(r"\b(?:facility|instrument|bank\s+loan|commercial\s+paper|debenture)\b", lowered)),
        "outlook": bool(re.search(r"\b(?:outlook|rating\s+watch|stable|negative|positive)\b", lowered)),
        "rationale": bool(re.search(r"\b(?:rationale|key\s+rating\s+driver|rating\s+sensitivity|liquidity)\b", lowered)),
    }
    hard_block = bool(re.search(r"\b(?:captcha|access denied|forbidden)\b", lowered))
    auth_marker = bool(re.search(r"\b(?:sign in|log in)\b", lowered))
    blocked = hard_block or (auth_marker and (len(text) < 1200 or sum(groups.values()) < 3))
    accepted = (
        len(text) >= 1200
        and groups["rating"]
        and groups["facility"]
        and groups["rationale"]
        and sum(groups.values()) >= 3
        and not blocked
    )
    return accepted, {
        "text_length": len(text), "positive_groups": groups,
        "blocked_marker": blocked, "hard_block_marker": hard_block,
        "auth_marker": auth_marker,
    }


def _clean_candidate(base_url: str, raw: str) -> str | None:
    value = raw.strip().strip("'\"")
    if not value or value.lower().startswith(("javascript:", "mailto:", "#")):
        return None
    url = urljoin(base_url, value)
    parsed = urlsplit(url)
    normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))
    return normalized if _allowed_target(normalized) else None


def _candidate_score(url: str, label: str) -> int:
    value = f"{url} {label}".lower()
    if any(word in value for word in BAD_LINK_WORDS):
        return -100
    return sum(4 if word == ".pdf" else 2 for word in REPORT_WORDS if word in value)


def extract_resolution_candidates(body: bytes, *, base_url: str) -> tuple[_Candidate, ...]:
    soup = BeautifulSoup(body, "html.parser")
    found: dict[tuple[str, str, tuple[tuple[str, str], ...]], _Candidate] = {}
    for node in soup.find_all(True):
        label = " ".join(node.stripped_strings)
        raw_values: list[str] = []
        for attribute in LINK_ATTRIBUTES:
            value = node.get(attribute)
            if isinstance(value, str):
                raw_values.append(value)
        onclick = node.get("onclick")
        if isinstance(onclick, str):
            raw_values.extend(
                match[1] for match in re.findall(
                    r"(?:window\.open|location(?:\.href)?)\s*(?:=|\()\s*(['\"])(.+?)\1",
                    onclick,
                )
            )
        for raw in raw_values:
            url = _clean_candidate(base_url, raw)
            if not url:
                continue
            score = _candidate_score(url, label)
            if score < 6:
                continue
            candidate = _Candidate(url, "GET", {}, "landing_link", score)
            found[(url, "GET", ())] = candidate

    for form in soup.find_all("form"):
        action = _clean_candidate(base_url, str(form.get("action") or base_url))
        if not action:
            continue
        label = " ".join(form.stripped_strings)
        score = _candidate_score(action, label)
        if score < 6:
            continue
        data = {
            str(item.get("name")): str(item.get("value") or "")
            for item in form.find_all(["input", "button"])
            if item.get("name") and str(item.get("type") or "").lower() not in {"reset"}
        }
        method = str(form.get("method") or "GET").upper()
        if method == "GET" and data:
            separator = "&" if urlsplit(action).query else "?"
            action = action + separator + urlencode(data)
            data = {}
        candidate = _Candidate(action, "POST" if method == "POST" else "GET", data, "download_form", score + 1)
        found[(candidate.url, candidate.method, tuple(sorted(data.items())))] = candidate
    return tuple(sorted(found.values(), key=lambda item: (-item.score, item.url))[:12])


def resolve_credit_rating(
    original_url: str,
    *,
    policy: DownloadPolicy | None = None,
    session: requests.Session | Any | None = None,
    max_requests: int = 12,
    browser_fallback: bool = True,
) -> CreditRatingResolution:
    """Resolve a credit report without treating an HTML landing page as failure."""

    if not _allowed_target(original_url):
        return CreditRatingResolution("TERMINAL_INVALID_CONTENT", original_url, None, (), "unapproved_host")
    policy = policy or DownloadPolicy()
    owned = session is None
    session = session or requests.Session()
    pending = [_Candidate(original_url, "GET", {}, "initial_request", 100)]
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    steps: list[ResolutionStep] = []
    last_html: DownloadResult | None = None
    try:
        while pending and len(steps) < max_requests:
            candidate = pending.pop(0)
            key = (candidate.url, candidate.method, tuple(sorted(candidate.data.items())))
            if key in seen:
                continue
            seen.add(key)
            referer = steps[-1].result.final_url if steps else None
            try:
                result = download_url(
                    candidate.url,
                    artifact_type="credit_rating",
                    policy=policy,
                    session=session,
                    method=candidate.method,
                    data=candidate.data,
                    referer=referer,
                )
            except BlockedTermsError:
                return CreditRatingResolution(
                    "SKIPPED_POLICY",
                    original_url,
                    None,
                    tuple(steps),
                    PUBLISHER_TERMS_ERROR,
                )
            if result.is_validated:
                selected = replace(result, requested_url=original_url)
                steps.append(ResolutionStep(candidate.step_type, candidate.method, candidate.url, result, True, {"score": candidate.score}))
                status = "DIRECT_REPORT_VALIDATED" if len(steps) == 1 else "LANDING_PAGE_RESOLVED"
                if result.final_url and urlsplit(result.final_url).hostname != urlsplit(original_url).hostname:
                    status = "TEMPORARY_OBJECT_REPORT_VALIDATED"
                return CreditRatingResolution(status, original_url, selected, tuple(steps))
            if result.detected_media_type == "text/html" and result.http_status == 200 and result.body:
                genuine, evidence = is_genuine_html_credit_report(
                    result.body, final_url=result.final_url or candidate.url
                )
                if genuine:
                    selected = replace(
                        result, requested_url=original_url, result_status="validated",
                        detected_media_type="text/html", error_code=None,
                        error_message=None,
                    )
                    steps.append(ResolutionStep(candidate.step_type, candidate.method, candidate.url, result, True, evidence))
                    return CreditRatingResolution("HTML_REPORT_VALIDATED", original_url, selected, tuple(steps))
                extracted = extract_resolution_candidates(
                    result.body, base_url=result.final_url or candidate.url
                )
                steps.append(ResolutionStep(candidate.step_type, candidate.method, candidate.url, result, False, {**evidence, "candidate_count": len(extracted)}))
                last_html = result
                pending.extend(item for item in extracted if (item.url, item.method, tuple(sorted(item.data.items()))) not in seen)
                continue
            steps.append(ResolutionStep(candidate.step_type, candidate.method, candidate.url, result, False, {"score": candidate.score}))
        if browser_fallback and last_html is not None:
            browser_result, browser_evidence = _browser_resolution(original_url, policy=policy)
            if browser_result is not None:
                steps.append(
                    ResolutionStep(
                        "browser_interaction", "BROWSER", original_url,
                        browser_result, True, browser_evidence,
                    )
                )
                status = (
                    "HTML_REPORT_VALIDATED"
                    if browser_result.detected_media_type == "text/html"
                    else "LANDING_PAGE_RESOLVED"
                )
                return CreditRatingResolution(status, original_url, browser_result, tuple(steps))
            if steps:
                last = steps[-1]
                steps[-1] = ResolutionStep(
                    last.step_type, last.method, last.request_url, last.result,
                    last.selected, {**dict(last.evidence), "browser": browser_evidence},
                )
        statuses = {step.result.http_status for step in steps}
        if statuses and statuses <= {404, 410}:
            status = "TERMINAL_INVALID_CONTENT"
        else:
            status = "INTERACTION_REQUIRED" if last_html is not None else "RETRYABLE_RESOLUTION_FAILURE"
        return CreditRatingResolution(status, original_url, None, tuple(steps), "no_validated_report_target")
    except Exception as exc:
        from ..core.host_request_contracts import AdministrativeDefer
        if isinstance(exc, AdministrativeDefer):
            exc.steps = tuple(steps)
        raise
    finally:
        if owned:
            session.close()
