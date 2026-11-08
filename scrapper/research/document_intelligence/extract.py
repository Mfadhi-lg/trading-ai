"""Bounded deterministic investment signals over canonical D03 content.

VERIFIED means an explicit numeric/event disclosure with exact source citations,
not that the issuer's assertion is independently true. Qualitative interpretations
are EVIDENCED_STATEMENT; ambiguous numeric binding is never silently accepted.
"""
from __future__ import annotations

from dataclasses import asdict
import re

from ...documents.credit_rating import normalize_credit_rating
from .schema import DocumentContext, ResearchEvidence
from .evidence import cell_evidence, element_evidence, text_evidence, validate_evidence, reconcile, canonical_hash
from .numbers import normalize_value, values_in
from .charts import chart_signals
from .context import PERIOD, UNIT, clauses, period_name, table_period, time_context, entity_scope
from .transcripts import parse as parse_transcript, roster as transcript_roster, name_key, exact_body, corroborated_body

# Recognition is intentionally broad; acceptance of a numeric relation is narrow.
RULES = {
    "growth_guidance": r"\bgrowth\b",
    "revenue_growth": r"\b(?:revenue|sales|value)\s+growth\b|\b(?:revenue|sales)\s+grew\b",
    "revenue": r"\b(?:revenue|sales|total (?:operating )?income|TOI|Op Income)\b",
    "operating_profit": r"\b(?:EBITDA|EBIT|operating profit|PBILDT)\b(?!\s+margin)",
    "profit": r"\b(?:profit after tax|net profit|net income|PAT)\b(?!\s+margin)",
    "operating_margin": r"\b(?:EBITDA|EBIT|operating|PBILDT)\s+margin[s]?(?:\d)?\b",
    "profit_margin": r"\b(?:PAT|net profit|net income)\s+margin[s]?(?:\d)?\b",
    "margin_outlook": r"\bmargin[s]?\b",
    "segment_profitability": r"\bsegment\s+(?:profit|margin|profitability)\b",
    "capacity_utilization": r"\b(?:capacity utili[sz]ation|utili[sz]ation of capacity)\b",
    "volume_growth": r"\bvolume\s+(?:growth|grew|increase|decline)\b",
    "domestic_performance": r"\bdomestic\b",
    "export_performance": r"\b(?:exports?|overseas)\b",
    "order_book": r"\b(?:net\s+)?order\s*book(?:\d)?\b",
    "order_inflows": r"\border\s+(?:inflows?|intake|booking|wins?)\b|\b(?:secure additional orders|new wins)\b",
    "execution_timeline": r"\b(?:execution|delivery|completion)\s+(?:timeline|schedule|by|in)\b",
    "capacity_expansion": r"\b(?:capacity expansion|expand\w*\s+capacity|new capacit\w*|greenfield)\b",
    "capex": r"\b(?:capex|capital expenditure|capital spending)\b",
    "new_facility": r"\b(?:new\s+(?:plant|facility|facilities|project)|commission\w*|operationalized\s+the\s+facility|greenfield)\b",
    "new_markets": r"\b(?:new markets|enter\w*\s+(?:new|the)\s+markets?)\b",
    "management_outlook": r"\b(?:outlook|optimistic|confident|expect|anticipate)\b",
    "project_delay": r"\b(?:delay\w*|postpone\w*|defer\w*)\b",
    "execution_bottleneck": r"\b(?:bottleneck|execution challenges|supply.chain disruption)\b",
    "working_capital": r"\b(?:working.capital|receivables|inventory days)\b",
    "customer_concentration": r"\b(?:customer concentration|top\s+\d+\s+customers|single customer)\b",
    "debt_funded_expansion": r"\bdebt.funded\b",
    "declining_margins": r"\b(?:declin\w*|fall\w*|lower|compress\w*)\s+margin[s]?\b",
    "slowing_orders": r"\b(?:slow\w*|declin\w*|lower)\s+order\w*\b",
    "demand_outlook": r"\b(?:demand|traction)\b",
    "pricing": r"\b(?:pricing|price increase|price reduction|realizations?)\b",
    "volume": r"\bvolumes?\b",
    "debt_reduction": r"\b(?:debt reduction|repay\w*|reduce\w*\s+debt|deleverag\w*)\b",
    "cash_flow": r"\b(?:cash.flow|cash accrual|cash generation)\b",
    "regulatory_risk": r"\b(?:regulatory|regulation|industry risk|approvals?)\b",
    "liquidity": r"\bliquidity\b",
    "debt": r"\b(?:debt|borrowings)\b",
    "leverage": r"\b(?:leverage|gearing)\b",
    "interest_coverage": r"\binterest coverage\b",
    "debt_servicing": r"\b(?:debt.servic\w*|DSCR|debt service coverage)\b",
    "credit_strengths": r"\b(?:key strengths|derive[s]? strength|rating strengths|credit strengths)\b",
    "credit_weaknesses": r"\b(?:key weaknesses|rating weaknesses|credit weaknesses|partially offset)\b",
    "rating_sensitivities": r"\b(?:rating sensitivities|positive factors|negative factors|upward factors|downward factors)\b",
    "manufacturing_capacity": r"\b(?:manufacturing capacity|production capacity|installed capacity|capacity of)\b",
    "funding_plan": r"\b(?:funded (?:by|through)|funding plan|internal accruals|debt funding)\b",
    "supply_chain_risk": r"\b(?:supply.chain|raw.material shortage)\b",
    "export_risk": r"\b(?:export.market|export challenges|export constraints)\b",
    "order_book_growth": r"\border.book\s+growth\b|\bgrowth\s+in\s+(?:the\s+)?order.book\b",
}
PATTERNS = {key: re.compile(value, re.I) for key, value in RULES.items()}
FOCUS_ONLY = {'manufacturing_capacity', 'funding_plan', 'supply_chain_risk', 'export_risk', 'order_book_growth'}
PRESENTATION = set(RULES) - {"liquidity", "leverage", "interest_coverage", "debt_servicing", "credit_strengths", "credit_weaknesses", "rating_sensitivities"} - FOCUS_ONLY
TRANSCRIPT = PRESENTATION
RATING = {"liquidity", "debt", "leverage", "interest_coverage", "debt_servicing", "working_capital", "cash_flow", "credit_strengths", "credit_weaknesses", "rating_sensitivities", "regulatory_risk"}
NUMERIC = {"growth_guidance", "revenue_growth", "revenue", "operating_profit", "profit", "operating_margin", "profit_margin", "segment_profitability", "capacity_utilization", "volume_growth", "order_book", "order_inflows", "capex", "debt", "leverage", "interest_coverage", "debt_servicing", "volume", 'order_book_growth'}
PERCENT = {"growth_guidance", "revenue_growth", "operating_margin", "profit_margin", "capacity_utilization", "volume_growth", 'order_book_growth'}
CURRENCY = {"revenue", "operating_profit", "profit", "order_book", "order_inflows", "capex", "debt"}
RISK_METRICS = {"project_delay", "execution_bottleneck", "working_capital", "customer_concentration", "debt_funded_expansion", "declining_margins", "slowing_orders", "regulatory_risk", "credit_weaknesses", "rating_sensitivities"}
HORIZON = re.compile(PERIOD.pattern + r"|\b(?:short|medium|long)[- ]term|multi[- ]year|next\s+(?:year|quarter|\d+\s+(?:months|years))\b", re.I)
FORWARD = re.compile(r"\b(?:guidance|guiding|expects?|expected|anticipat\w*|will|plans? to|planned|forecast\w*|projected|projects? that|budget\w*)\b", re.I)
TARGET = re.compile(r"\b(?:target\w*|aim\w*|aspir\w*)\b", re.I)
NEGATED_GUIDANCE = re.compile(r"\b(?:no|not|never|don't|do not)\b.{0,45}\b(?:guidance|guidances|giving)\b", re.I)
WITHDRAWAL = re.compile(r"\b(?:withdraw\w*|suspend\w*)\b.{0,25}\b(?:guidance|forecast)\b|\b(?:guidance|forecast)\b.{0,25}\b(?:withdraw\w*|suspend\w*)\b", re.I)


def _new(context, metric, kind, status, **kwargs):
    return ResearchEvidence(source=context.to_dict(), metric=metric, statement_kind=kind,
                            status=status, reporting_period=kwargs.pop("reporting_period", context.reporting_period), **kwargs)


def _kind(text, metric, role=None, section=None):
    if role == "analyst" or (role not in {"management", "rating_agency"} and section == "qa" and "?" in text):
        return "ANALYST_QUESTION", "UNKNOWN"
    if role not in {None, "management", "rating_agency"}:
        return "UNKNOWN", "UNKNOWN"
    risk = re.search(r"\b(?:risk|pressure|weakness|bottleneck|deterioration|weakening|delays?|disruption|concentration|debt.funded|declining|slowing)\b", text, re.I)
    favourable = re.search(r"\b(?:negligible|comfortable|healthy|strong|adequate|improved)\b", text, re.I)
    if risk and not (favourable and not re.search(r"\b(?:deterioration|weakening|delays?|disruption)\b", text, re.I)):
        basis = 'CONDITIONAL' if re.search(r"\b(?:if|below|above|likely|may|could)\b", text, re.I) else 'ACTUAL' if re.search(r'\b(?:witnessed|experienced|occurred|were|was)\b', text, re.I) else 'UNKNOWN'
        return "RISK_WARNING", basis
    if WITHDRAWAL.search(text):
        return "GUIDANCE", "PROJECTED"
    if TARGET.search(text):
        return "TARGET", "PROJECTED"
    if (re.search(r"\b(?:if|subject to|may|could)\b", text, re.I) and FORWARD.search(text)) or re.search(r'\b(?:may|could|would)\s+(?:reach|grow|generate|deliver|be|increase)\b', text, re.I):
        return "CONDITIONAL_FORECAST", "CONDITIONAL"
    if FORWARD.search(text) and not NEGATED_GUIDANCE.search(text):
        return "GUIDANCE", "PROJECTED"
    if re.search(r"\b(?:grew|was|were|achieved|reported|stands|stood|recorded|as of|as on|ended|registered|operationalized|secured|completed)\b", text, re.I) or list(values_in(text)):
        return "HISTORICAL_FACT", "ACTUAL"
    return "QUALITATIVE_OPINION", "UNKNOWN"


def _bind_number(text, metric):
    """Require an adjacent unit-bearing value and exclude intervening metrics."""
    labels = list(PATTERNS[metric].finditer(text))
    possible, direct_preceding = [], []
    for value, normalized in values_in(text):
        if metric in PERCENT and normalized["unit"] != "percent":
            continue
        if metric in CURRENCY and normalized["unit"] != "currency":
            continue
        if metric in {"leverage", "interest_coverage", "debt_servicing"} and normalized["unit"] != "ratio":
            continue
        for label in labels:
            gap = text[label.end():value.start()] if value.start() >= label.end() else (
                text[value.end():label.start()] if value.end() <= label.start() else None)
            if gap is None or len(gap) > 50:
                continue
            if value.end() <= label.start() and gap.strip() and not (metric == 'capex' and re.fullmatch(r'\s*of\s*', gap, re.I)):
                # '808 crores, providing revenue visibility' is not revenue.
                # A value before its label is accepted only as a direct label.
                continue
            if value.end() <= label.start():
                prior_text = text[max(0, value.start() - 50):value.start()]
                if any(p.search(prior_text) for k, p in PATTERNS.items() if k in (NUMERIC | {"pricing"}) and k != metric):
                    continue
            if re.search(r"[.!?;\n]", gap) or any(p.search(gap) for k, p in PATTERNS.items() if k in (NUMERIC | {"pricing"}) and k != metric):
                continue
            if re.search(r"\band\s+[A-Z]{2,}\b", gap):
                continue
            possible.append((value, normalized))
            if value.end() <= label.start():
                direct_preceding.append((value, normalized))
            break
    if direct_preceding:
        possible = direct_preceding
    unique = {(m.start(), m.end()): (m, n) for m, n in possible}
    return list(unique.values())


def _signals(context, text, evidence, *, metrics, speaker=None, section=None, dimensions=None, inherited_period=None, inherited_metric=None):
    text = text.strip().lstrip('•✓').strip()
    # Preserve original whitespace in a smaller exact claim span. Identity and
    # context citations remain separate rather than widening the numeric claim.
    narrowed = []
    for ev in evidence:
        if ev.get('kind') in {'page_text', 'element_text'} and ev.get('quote'):
            match = re.search(r'\s+'.join(re.escape(t) for t in text.split()), ev['quote'])
            if match:
                ev = {**ev, 'start': ev['start'] + match.start(), 'end': ev['start'] + match.end(), 'quote': match.group()}
        narrowed.append(ev)
    evidence = narrowed
    # Two explicit currency amounts in a comparison are two observations, not
    # a range. Keep the whole exact sentence as evidence for the shared label.
    labels = [k for k in metrics & (CURRENCY | PERCENT) if PATTERNS[k].search(text)]
    comparison_values = [(m, n) for m, n in values_in(text)
                         if len(labels) == 1 and n['unit'] == ('currency' if labels[0] in CURRENCY else 'percent')]
    if len(comparison_values) == 2 and len(labels) == 1 and (speaker or {}).get('role') != 'analyst':
        first, second = [m for m, _ in comparison_values]
        between = text[first.end():second.start()]
        connector = re.search(r'\b(?:from|to|as compared to|compared (?:to|with))\b', between, re.I)
        if connector and not any(p.search(between) for k, p in PATTERNS.items() if k in NUMERIC and k != labels[0]):
            cut = first.end() + connector.start()
            left, right = text[:cut], text[cut:]
            left_period, right_period = time_context(left)[0], time_context(right)[0]
            # A "from" comparative may omit the current period only when an
            # explicit financial context anchor has already established it.
            if not left_period and connector[0].lower() == 'from':
                left_period = inherited_period
            if left_period and right_period and left_period != right_period:
                return [r for part, period in [(left, left_period), (right, right_period)]
                        for r in _signals(context, part, evidence, metrics={labels[0]}, speaker=speaker,
                                          section=section, dimensions=dimensions, inherited_period=period,
                                          inherited_metric=labels[0])]
    comparisons = re.split(r'\bas compared to\b', text, flags=re.I)
    if len(comparisons) == 2 and (speaker or {}).get('role') != 'analyst':
        labels = [k for k in metrics & NUMERIC if PATTERNS[k].search(comparisons[0])]
        labels = [k for k in labels if k in CURRENCY]
        if len(labels) == 1 and all(len([n for _, n in values_in(p) if n['unit'] == 'currency']) == 1 for p in comparisons):
            return [r for part in comparisons for r in _signals(context, part, evidence, metrics={labels[0]},
                speaker=speaker, section=section, dimensions=dimensions, inherited_period=inherited_period,
                inherited_metric=labels[0])]
    parts = clauses(text)
    if len(parts) > 1:
        result = []
        local_period = inherited_period
        for part in parts:
            result.extend(_signals(context, part, evidence, metrics=metrics, speaker=speaker, section=section,
                                   dimensions=dimensions, inherited_period=local_period))
            # Agency HTML elements may contain many unrelated paragraphs and
            # repeated tables. Only explicit periods within each claim apply.
            if (speaker or {}).get('role') != 'rating_agency' and period_name(part) and _kind(part, '', (speaker or {}).get('role'), section)[1] == 'ACTUAL':
                local_period = period_name(part)
        return result
    output = []
    for metric in sorted(metrics):
        if not PATTERNS[metric].search(text) and metric != inherited_metric:
            continue
        if metric == "growth_guidance" and not FORWARD.search(text):
            continue
        if metric == "growth_guidance" and (PATTERNS["revenue_growth"].search(text) or PATTERNS["volume_growth"].search(text)):
            continue
        kind, basis = _kind(text, metric, (speaker or {}).get("role"), section)
        horizons = list(dict.fromkeys(re.sub(r"\s+", " ", h.group()).upper() for h in HORIZON.finditer(text)))
        horizon = (period_name(horizons[0]) or horizons[0]) if len(horizons) == 1 and basis in {"PROJECTED", "CONDITIONAL"} else None
        if horizon and re.search(r'\bsecond half\b', text, re.I) and re.fullmatch(r'FY\d{2}', horizon):
            horizon = 'H2' + horizon
        if not horizon and basis == 'PROJECTED' and inherited_period and re.search(r'\bcurrent (?:financial|fiscal) year\b', text, re.I):
            horizon = inherited_period[-4:]
        reasons = []
        if len(horizons) > 1:
            reasons.append("multiple_time_references")
        status = "EVIDENCED_STATEMENT"
        original, normalized, currency, unit, scale = None, None, None, None, None
        dimensions = dict(dimensions or {})
        scope = entity_scope(text)
        if scope:
            dimensions['entity_scope'] = scope
        if metric == 'working_capital':
            dimensions['polarity'] = 'FAVOURABLE' if re.search(r'\b(?:negligible|comfortable|healthy|improved)\b', text, re.I) else 'ADVERSE' if kind == 'RISK_WARNING' else 'UNKNOWN'
        if re.search(r"\b(?:higher|lower|improvement|increase|decline)\b.{0,35}\bby\b", text, re.I):
            dimensions["measurement_kind"] = "change"
        elif re.search(r"\b(?:avg|average|CAGR)\b", text, re.I):
            dimensions["measurement_kind"] = "aggregate"
        else:
            dimensions["measurement_kind"] = "level"
        if metric in NUMERIC and kind not in {"ANALYST_QUESTION", "UNKNOWN"}:
            candidates = _bind_number(text, metric) if PATTERNS[metric].search(text) else list(values_in(text))
            candidates = [(m, n) for m, n in candidates if metric not in CURRENCY or n['unit'] == 'currency']
            if len(candidates) == 1:
                match, number = candidates[0]
                original = match.group().strip()
                for ev in evidence:
                    located, _ = exact_body(ev.get('quote') or '', original)
                    if located:
                        original = located
                        break
                normalized, currency, unit, scale = (number[k] for k in ("normalized_value", "currency", "unit", "scale"))
                dimensions["approximate"] = number["approximate"]
                labels = list(PATTERNS[metric].finditer(text))
                nearest = min(labels, key=lambda label: abs(label.end() - match.start())) if labels else None
                relation = text[nearest.end():match.start()] if nearest and nearest.end() <= match.start() else ""
                if re.search(r"\bto\b", relation, re.I):
                    dimensions["measurement_kind"] = "level"
                elif re.search(r"\bby\b", relation, re.I):
                    dimensions["measurement_kind"] = "change"
                status = "VERIFIED"
                if any(e.get("text_origin") == "ocr" for e in evidence):
                    status = "AMBIGUOUS"
                    reasons.append("ocr_numeric_value_requires_manual_validation")
            elif len(candidates) > 1:
                status = "AMBIGUOUS"
                reasons.append("multiple_values_without_unique_metric_binding")
            elif list(values_in(text)):
                status = "AMBIGUOUS"
                reasons.append("numeric_metric_relation_or_unit_not_supported")
        if NEGATED_GUIDANCE.search(text) and kind != "ANALYST_QUESTION":
            # A refusal to issue guidance is not a forecast and not a withdrawal.
            kind, basis, status = "QUALITATIVE_OPINION", "UNKNOWN", "EVIDENCED_STATEMENT"
            original, normalized, currency, unit, scale = None, None, None, None, None
            reasons.append("guidance_not_offered_not_withdrawn")
        if WITHDRAWAL.search(text) and (speaker or {}).get("role") == "management":
            kind, basis, status = "GUIDANCE", "PROJECTED", "EVIDENCED_STATEMENT"
            reasons.append("explicit_guidance_withdrawal")
        period, asof = time_context(text)
        if not period and inherited_period and (q := re.search(r'\b(Q[1-4])\b(?!\s*(?:FY|[\d\'’]))', text, re.I)):
            period = q[1].upper() + inherited_period[-4:]
        if basis in {'PROJECTED', 'CONDITIONAL'}:
            period = None
        elif not period and not asof and not PERIOD.search(text):
            period = inherited_period
        if metric in {'order_book', 'order_inflows'} and not PERIOD.search(text):
            period = None
        output.append(_new(context, metric, kind, status, evidence=evidence, reporting_period=period,
                           original_value=original, normalized_value=normalized, currency=currency,
                           unit=unit, scale=scale, value_basis=basis, time_horizon=horizon,
                           dimensions=dimensions, speaker=speaker, section=section, reasons=reasons, as_of_date=asof, claim_text=text))
    return output


def _table_signals(context, page, element, metrics, *, focus_disclosures=False):
    rows = element.get("rows") or []
    if not rows:
        return []
    output = []
    numeric_metrics = NUMERIC | ({'manufacturing_capacity', 'capacity_expansion'} if focus_disclosures else set())
    # Determine the header band from actual native cells, not fixed row count.
    numeric = re.compile(r"^[~₹$\s(+-]*[\d,]+(?:\.\d+)?[)%\s]*$")
    first = next((i for i, row in enumerate(rows) if i and row and row[0].strip()
                  and any(numeric.fullmatch(x.strip()) for x in row[1:] if x.strip())), None)
    if first is None:
        return [_new(context, 'table_layout', 'UNKNOWN', 'UNSUPPORTED',
                     evidence=[element_evidence(page, element)], reasons=['no_supported_header_value_layout'])]
    header_rows = rows[:first]
    width = max(map(len, rows))
    annual = any('brief financial' in x.lower() for row in header_rows for x in row)
    scope = next((entity_scope(x) for row in header_rows for x in row if entity_scope(x)), None)
    common_units = [(r, c, m.group()) for r, row in enumerate(header_rows) for c, x in enumerate(row)
                    if (m := UNIT.search(x)) and '%' not in m.group() and m.group().lower() not in {'times', 'x'}]
    common_hint = common_units[0][2] if len({x[2].lower() for x in common_units}) == 1 else None
    groups = {}
    group = None
    group_cell = None
    for c in range(1, width):
        top = rows[0][c] if c < len(rows[0]) else ''
        matches = [k for k in metrics & numeric_metrics if PATTERNS[k].search(top)]
        if len(matches) == 1:
            group, group_cell = matches[0], (0, c)
        elif top.strip():
            group, group_cell = None, None
        groups[c] = (group, group_cell)
    for r, row in enumerate(rows[first:], first):
        if not row or not row[0].strip():
            continue
        row_metrics = [k for k in metrics & numeric_metrics if PATTERNS[k].search(row[0])]
        for c, raw in enumerate(row[1:], 1):
            if not raw.strip() or not numeric.fullmatch(raw.strip()):
                continue
            selected = row_metrics or ([groups[c][0]] if groups.get(c, (None,))[0] else [])
            column_cells = [(h, c) for h, rr in enumerate(header_rows) if c < len(rr) and rr[c].strip()]
            column = ' '.join(rows[h][cc] for h, cc in column_cells)
            period = table_period(column, annual=annual)
            if focus_disclosures and len({period_name(m.group()) for m in PERIOD.finditer(column)}) > 1:
                period = None
            for metric in selected:
                hint_match = UNIT.search(row[0]) or UNIT.search(column)
                hint = hint_match.group() if hint_match else common_hint
                number = normalize_value(raw, unit_hint=hint)
                if number and ((metric in PERCENT and number['unit'] != 'percent') or
                               (metric in CURRENCY and number['unit'] != 'currency')):
                    number = None
                locators = [(r, 0), (r, c)] + column_cells
                if not row_metrics and groups[c][1]:
                    locators.append(groups[c][1])
                if common_hint and not hint_match:
                    locators += [(h, cc) for h, cc, _ in common_units]
                if scope:
                    locators += [(h, cc) for h, rr in enumerate(header_rows) for cc, x in enumerate(rr) if entity_scope(x)]
                ev = [cell_evidence(page, element, h, cc) for h, cc in dict.fromkeys(locators)]
                kind, basis = _kind(column + ' ' + row[0], metric)
                if kind not in {'GUIDANCE', 'TARGET', 'CONDITIONAL_FORECAST'}:
                    kind, basis = 'HISTORICAL_FACT', 'ACTUAL'
                flow = metric in {'revenue', 'operating_profit', 'profit', 'volume', 'capex'}
                dims = {'row_label': row[0], 'column_label': column,
                        'measurement_kind': 'level',
                        'period_aggregation': 'CUMULATIVE' if flow and period and period.startswith(('9M', 'H')) else 'ANNUAL' if flow and period and period.startswith('FY') else 'QUARTER' if flow and period and period.startswith('Q') else 'NOT_ESTABLISHED',
                        'approximate': number['approximate'] if number else None}
                row_scope = entity_scope(row[0]) if not row_metrics else None
                if not row_metrics and not row_scope:
                    # Row is an entity in a metric-column table; don't attribute
                    # it to the document issuer. Preserve named subsidiaries.
                    row_scope = re.split(r'\s*\(', row[0])[0].strip()
                if row_scope or scope:
                    dims['entity_scope'] = row_scope or scope
                if re.search(r'\brestated\b', page['selected_text'], re.I):
                    dims['restated_comparatives'] = True
                    m = re.search(r'[^\n]*restated[^\n]*', page['selected_text'], re.I)
                    ev.append(text_evidence(page, m.group(), start=m.start()))
                status = 'VERIFIED' if number and period else 'AMBIGUOUS'
                reasons = [] if status == 'VERIFIED' else ['table_unit_or_period_not_explicit']
                if element['text_origin'] == 'ocr':
                    status, reasons = 'AMBIGUOUS', ['ocr_table_requires_manual_validation']
                output.append(_new(context, metric, kind, status, evidence=ev,
                    original_value=raw, normalized_value=number['normalized_value'] if number else None,
                    currency=number['currency'] if number else None, unit=number['unit'] if number else None,
                    scale=number['scale'] if number else None, value_basis=basis,
                    reporting_period=period if basis == 'ACTUAL' else None,
                    time_horizon=period if basis != 'ACTUAL' else None, dimensions=dims, reasons=reasons))
    return output


def _transcript_signals(context, extraction, metrics=None):
    transcript = parse_transcript(extraction)
    pages = {p["page_or_slide_number"]: p for p in extraction["pages"]}
    roster = transcript_roster(extraction)
    call_period, call_evidence = None, None
    for page in extraction['pages'][:3]:
        for m in re.finditer(r'[^\n]+', page['selected_text']):
            if re.search(r'earnings|results conference|financial year', m.group(), re.I) and period_name(m.group()):
                call_period = period_name(m.group())
                call_evidence = text_evidence(page, m.group(), start=m.start())
                break
        if call_period:
            break
    output = []
    # Reuse the existing segmenter; opening-remarks declarations supplement its
    # strict heading grammar without changing the production segmentation code.
    opening = next(((p, m) for p in extraction["pages"] for m in re.finditer(r"\bopening remarks\b", p["selected_text"], re.I)), None)
    for segment in transcript.segments:
        label = segment.speaker_raw
        identity = roster.get(name_key(label), {})
        role = identity.get("role", "management" if label.lower() == "management" else "UNKNOWN")
        if label.lower() == "analyst":
            role = "analyst"
        if label.lower() in {"moderator", "operator"}:
            role = "moderator"
        speaker = {"name": label if label.lower() not in {"management", "moderator", "operator"} else None,
                   "label": label, "role": role, "title": identity.get("title")}
        section = segment.section_kind
        inherited_period = call_period if role == 'management' else None
        inherited_evidence = call_evidence if inherited_period else None
        # An explicit handoff to financials/annual performance supersedes a
        # quarterly call cover. Do not derive annual context from the cover date.
        if role == 'management':
            anchors = [(p, m) for p in extraction['pages'] if segment.page_start - 1 <= p['page_or_slide_number'] <= segment.page_start
                       for m in re.finditer(r'[^\n]*(?:financials|performance in|financial performance)[^\n]*',
                                           p['selected_text'][:p['selected_text'].find(label + ':')] if p['page_or_slide_number'] == segment.page_start and label + ':' in p['selected_text'] else p['selected_text'], re.I)
                       if period_name(m.group())]
            if anchors:
                p, m = anchors[-1]
                inherited_period = period_name(m.group())
                inherited_evidence = text_evidence(p, m.group(), start=m.start())
        previous_margin = None
        topic_scope = identity.get('entity_scope')
        scope_evidence = identity.get('identity_evidence') if topic_scope else None
        section_evidence = None
        if section == "unknown" and opening and role == "management" and segment.page_start >= opening[0]["page_or_slide_number"]:
            section = "prepared_remarks"
            section_evidence = text_evidence(opening[0], opening[1].group(), start=opening[1].start())
        # Preserve page boundaries: a statement can join fragments on one page;
        # cross-page numeric interpretation remains an explicit unsupported case.
        per_page = {}
        for f in segment.fragments:
            per_page.setdefault(f.page_number, []).append(f)
        for page_number, fragments in per_page.items():
            bodies = []
            for f in fragments:
                page = pages[page_number]
                element = next((e for e in page["elements"] if e["element_position"] == f.element_position), None)
                container = element["text_raw"] if element else page["selected_text"]
                body, offset = exact_body(container, f.body_raw)
                # Native text blocks may split glyph tokens which are intact in
                # the canonical page text. Use the intact source slice directly.
                native_body, native_offset = corroborated_body(page['selected_text'], f.body_raw)
                if native_body is not None and re.search(r'\d\s+\d', f.body_raw) and not re.search(r'\d\s+\d', native_body):
                    body, offset, element = native_body, native_offset, None
                if body is None:
                    # Fragment compaction cannot be silently used as an exact span.
                    output.append(_new(context, "transcript_layout", "UNKNOWN", "UNSUPPORTED",
                        reasons=["compacted_fragment_not_exactly_locatable"], section=section, speaker=speaker))
                    continue
                bodies.append((body, text_evidence(page, body, element=element, start=offset)))
            bodies = [(re.sub(r"\s+", " ", body), ev) for body, ev in bodies]
            joined = " ".join(body for body, _ in bodies)
            spans = []
            offset = 0
            for body, ev in bodies:
                spans.append((offset, offset + len(body), ev))
                offset += len(body) + 1
            for sentence in re.finditer(r".+?(?:[.!?](?=\s+[A-Z]|$)|$)", joined):
                text = sentence.group().strip()
                if not text:
                    continue
                evidence = [ev for start, end, ev in spans if start < sentence.end() and end > sentence.start()]
                exact_sentence, sentence_offset = exact_body(pages[page_number]['selected_text'], text)
                if exact_sentence:
                    evidence.append(text_evidence(pages[page_number], exact_sentence, start=sentence_offset))
                if identity.get("identity_evidence"):
                    evidence.append(identity["identity_evidence"])
                if section_evidence:
                    evidence.append(section_evidence)
                if inherited_evidence:
                    evidence.append(inherited_evidence)
                topic = re.search(r'\b(?:on|in)\s+([A-Za-z0-9&]+)\s+(?:side|segment)\b', text, re.I)
                if topic and role == 'management':
                    topic_scope = topic[1]
                    scope_evidence = min((ev for ev in evidence if topic[0] in ev.get('quote', '')), key=lambda ev: len(ev['quote']), default=None)
                dims = {'entity_scope': topic_scope} if topic_scope else {}
                explicit_scope = entity_scope(text)
                if explicit_scope and role == 'management':
                    topic_scope = explicit_scope
                    dims['entity_scope'] = explicit_scope
                    scope_evidence = min((ev for ev in evidence if entity_scope(ev.get('quote', '')) == explicit_scope), key=lambda ev: len(ev['quote']), default=None)
                if scope_evidence:
                    evidence.append(scope_evidence)
                if previous_margin and re.search(r'\bexpect this to\s+improve', text, re.I):
                    output.extend(_signals(context, text, evidence + previous_margin, metrics={'margin_outlook'},
                        inherited_metric='margin_outlook', speaker=speaker, section=section, dimensions=dims))
                output.extend(_signals(context, text, evidence, metrics=metrics if metrics is not None else TRANSCRIPT, speaker=speaker, section=section,
                                       dimensions=dims, inherited_period=inherited_period))
                previous_margin = evidence if PATTERNS['operating_margin'].search(text) else None
                periods = list(PERIOD.finditer(text))
                if len(periods) == 1 and not FORWARD.search(text) and not re.search(r'\b(?:from|compared)\b', text, re.I):
                    inherited_period = period_name(text)
                    inherited_evidence = next((ev for ev in evidence if PERIOD.search(ev.get('quote') or '')), None)
    if not transcript.segments:
        output.append(_new(context, "speaker_attribution", "UNKNOWN", "UNSUPPORTED", reasons=["no_explicit_speaker_labels"]))
    if any(s.page_start != s.page_end for s in transcript.segments):
        output.append(_new(context, "cross_page_numeric_binding", "UNKNOWN", "UNSUPPORTED", reasons=["only_same_page_sentence_binding_supported"]))
    return output, {"segmenter_status": transcript.status, "segments": len(transcript.segments),
                    "boundaries": [asdict(b) for b in transcript.boundaries], "validations": list(transcript.validations)}


def _rating_signals(context, extraction):
    normalized = normalize_credit_rating(extraction)
    output = []
    pages = {p["page_or_slide_number"]: p for p in extraction["pages"]}
    for ev in normalized.document_evidence:
        page = pages[ev.page_number]
        element = next((e for e in page["elements"] if e["element_position"] == ev.element_position), None)
        text = element.get("text_raw") if element else page["selected_text"]
        if ev.source_value_raw in (text or ""):
            output.append(_new(context, "rating_" + ev.field_code, "RATING_OBSERVATION", "EVIDENCED_STATEMENT",
                evidence=[text_evidence(page, ev.source_value_raw, element=element)],
                original_value=ev.source_value_raw, normalized_value=ev.source_value_raw,
                effective_date=normalized.action_date, reasons=["rating_as_of_observation_not_current_status"]))
    from .ratings import events
    output.extend(events(context, extraction, normalized))
    # An unchanged rating newly published is not a change. No comparison of grades
    # across agencies/facilities is performed; only explicit action columns count.
    for passage in normalized.passages:
        page = pages[passage.page_number]
        element = next(e for e in page["elements"] if e["element_position"] == passage.element_position)
        quote = element["text_raw"]
        if not quote:
            continue
        output.extend(_signals(context, quote, [text_evidence(page, quote, element=element)], metrics=RATING,
                               speaker={"name": normalized.agency_raw, "role": "rating_agency", "title": None},
                               section=passage.passage_kind, dimensions={"rating_as_of": normalized.action_date}))
    return output, {"normalizer_status": normalized.status, "agency": normalized.agency_code,
                    "action_date": normalized.action_date, "document_class": normalized.document_class,
                    "validations": list(normalized.validations)}


def extract_intelligence(context: DocumentContext, extraction: dict, *, focus_disclosures=False):
    """Pure in-memory entry point. Accepts ExtractedDocument.to_dict or D03 export."""
    pages = extraction.get("pages") or []
    if [p["page_or_slide_number"] for p in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("Canonical pages must be ordered and contiguous")
    output, diagnostics = [], {}
    metrics = RATING if context.document_type == "credit_rating" else PRESENTATION
    if focus_disclosures:
        from .disclosures import FOCUSED_METRICS
        metrics = (metrics | FOCUS_ONLY) & FOCUSED_METRICS
    if extraction.get("extraction_status") == "ok" and context.document_type == "transcript":
        output, diagnostics = _transcript_signals(context, extraction, metrics=metrics)
    elif extraction.get("extraction_status") == "ok" and context.document_type == "credit_rating":
        output, diagnostics = _rating_signals(context, extraction)
        if focus_disclosures and diagnostics.get('agency'):
            from .disclosures import sensitivity_passages
            for text, evidence in sensitivity_passages(extraction):
                output.extend(_signals(context,text,evidence,metrics={'rating_sensitivities'},
                    speaker={'name':diagnostics['agency'],'role':'rating_agency'},section='rating_sensitivities'))
        for page in pages:
            for element in page['elements']:
                if element['element_kind'] == 'table':
                    try:
                        output.extend(_table_signals(context, page, element, metrics if focus_disclosures else RATING | NUMERIC, focus_disclosures=focus_disclosures))
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        output.append(_new(context, 'table_layout', 'UNKNOWN', 'UNSUPPORTED',
                            evidence=[element_evidence(page, element)], reasons=['invalid_or_unsupported_table_element', type(exc).__name__]))
    elif context.document_type != "presentation":
        output.append(_new(context, "document_extraction", "UNKNOWN", "UNSUPPORTED", reasons=["segmentation_or_rating_normalization_requires_wholly_usable_extraction"]))
    else:
        for page in pages:
            title = page['selected_text'].splitlines()[0] if page['selected_text'] else ''
            local_period = period_name(title) if re.search(r'financial|performance|quarterly result', title, re.I) else None
            title_ev = text_evidence(page, title) if local_period else None
            for line in re.finditer(r"[^\n]+", page["selected_text"]):
                ev = [text_evidence(page, line.group(), start=line.start())]
                if title_ev:
                    ev.append(title_ev)
                output.extend(_signals(context, line.group(), ev, metrics=metrics, section="presentation", inherited_period=local_period))
            if focus_disclosures:
                from .disclosures import wrapped_passages
                for text, ev in wrapped_passages(page):
                    output.extend(_signals(context, text, ev, metrics=metrics, section='presentation'))
            for continuation in re.finditer(r'[^\n]*order\s*book\d?\s*\n\s*grows to[^\n]*', page['selected_text'], re.I):
                output.extend(_signals(context, re.sub(r'\s+', ' ', continuation.group()),
                    [text_evidence(page, continuation.group(), start=continuation.start())], metrics={'order_book'}, section='presentation'))
            for element in page["elements"]:
                if element["element_kind"] == "table":
                    try:
                        output.extend(_table_signals(context, page, element, metrics, focus_disclosures=focus_disclosures))
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        output.append(_new(context, 'table_layout', 'UNKNOWN', 'UNSUPPORTED',
                            evidence=[element_evidence(page, element)],
                            reasons=['invalid_or_unsupported_table_element', type(exc).__name__],
                            dimensions={'page': page['page_or_slide_number'], 'element': element['element_position']}))
                elif element["element_kind"] in {"image", "chart"}:
                    chart_records = chart_signals(context, page, element, PATTERNS, metrics & NUMERIC) if element["element_kind"] == "chart" else []
                    if chart_records:
                        output.extend(chart_records)
                        continue
                    output.append(_new(context, "visual_content", "UNKNOWN", "UNSUPPORTED",
                        evidence=[element_evidence(page, element)], reasons=["no_reliable_semantic_numeric_mapping_from_visual"], section="presentation"))
            if page["selected_text_origin"] == "none":
                output.append(_new(context, "page_content", "UNKNOWN", "UNSUPPORTED", reasons=["no_usable_native_or_ocr_text"], dimensions={"page": page["page_or_slide_number"]}))
    if focus_disclosures:
        from .disclosures import qualify, review_passages
        output = qualify(output, context, extraction)
        output.extend(review_passages(context, extraction))
        diagnostics['extraction_profile'] = 'investment-disclosures-v1'
    found = {r.metric for r in output}
    missing_metrics = metrics | ({"rating_action"} if context.document_type == "credit_rating" else set())
    if focus_disclosures:
        categories = {'order_book','management_guidance','capex_capacity','credit_rating','operational_risk'}
        found_categories = {c for r in output if not r.dimensions.get('evidence_only')
                            for c in r.dimensions.get('disclosure_categories',[])}
        missing_metrics = {'category_'+c for c in categories-found_categories}
    for metric in sorted(missing_metrics):
        if metric not in found:
            status = "NOT_FOUND" if extraction.get("extraction_status") == "ok" else "UNKNOWN"
            output.append(_new(context, metric, "UNKNOWN", status,
                reasons=["no_supported_disclosure_in_inspected_content" if status == "NOT_FOUND" else "incomplete_content_cannot_establish_absence"]))
    extraction_digest = canonical_hash(extraction)
    accepted, rejected = [], []
    for record in output:
        if focus_disclosures:
            record.parser_version = ('phase5-disclosure-review-1.0.0' if record.dimensions.get('evidence_only')
                                     else 'phase5-disclosures-1.0.0')
            if context.provenance.get('publication_evidence'):
                record.source['publication_evidence'] = dict(context.provenance['publication_evidence'])
        record.source["canonical_extraction_sha256"] = extraction_digest
        record.source["canonical_parser_name"] = extraction.get("parser_name", "document-structure")
        record.source["canonical_parser_version"] = extraction.get("parser_version", "1.1.0")
        if not validate_evidence(record, extraction):
            rejected.append({'metric': record.metric, 'evidence': record.evidence,
                             'reason': 'invalid_evidence_record_rejected'})
            # Quarantine carries no replacement citation or financial value.
            quarantine = _new(context, 'evidence_rejection', 'UNKNOWN', 'UNSUPPORTED',
                reasons=['invalid_evidence_record_rejected'],
                dimensions={'rejected_metric': record.metric,
                            'pages': sorted({e.get('page') for e in record.evidence if e.get('page') is not None})})
            quarantine.source = record.source
            if focus_disclosures:
                quarantine.parser_version = 'phase5-disclosures-1.0.0'
                quarantine.dimensions['disclosure_categories'] = record.dimensions.get('disclosure_categories', [])
            accepted.append(quarantine)
            continue
        accepted.append(record)
    output = accepted
    output = reconcile(output)
    diagnostics["evidence_validation"] = {"checked": len(output), "failed": len(rejected), 'rejected_records': rejected}
    diagnostics["point_in_time_basis"] = "capture_safe_only; unknown publication time is not backdated to filing/reporting date"
    return output, diagnostics
