# One-request authorization — Phase 4

**STOPPED BEFORE INFERENCE. No request has been sent and no inference charges incurred.**

Source: Shriram Properties Limited (SHRIRAMPPS), issuer credit-rating notice dated **30 October 2025**, reporting CRISIL's assessment. Existing local PDF; no download. Original SHA-256: `71908f4b1194a5361f69bc94aa7e5d0fe92f1d73e087074fbfdc7c93413acc87`. The acquisition-only exposure and experiment search are recorded in the [frozen selection](../../artifacts/credit-rating-intelligence-v1/phase4/source/selection.json).

Coverage: **physical page 1 of 1**, full native page text plus all four detected table matrices, 43 addressable regions. Four damaged-text regions are explicitly UNRESOLVED. No images/OCR; signature/logo content is not interpreted. This is a short issuer notice, not the full agency rationale. The request contains no expected answers or prior company AI output.

Route: existing OpenRouter transport → `google/gemini-3.8-flash` → **`google-ai-studio/flex` only**, low reasoning, JSON output, no fallback/retries/tools/search/Batch. Public [endpoint catalog](https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints) checked 24 October 2026: route listed, status 0, 100% reported recent/day uptime, 1,048,576 context tokens, 65,536 maximum completion tokens. This is availability metadata, not a successful inference probe or account-credit check.

Frozen request: [payload.json](../../artifacts/credit-rating-intelligence-v1/phase4/prepared/payload.json), **32,027 exact transport bytes**. SHA-256:

`4eb1a99124a9a200f74416380d41ce8cba179032318d98627b2a6f64b2ccc802`

Budget: ~9,151 estimated input tokens; conservative admission allowance **34,075**. Expected output including reasoning **4,096**; enforced shared completion cap **8,192**. Prices capped at **$0.375/M input**, **$1.875/M output**, matching the checked route. Estimated inference cost **$0.01112**; conservative calculation **$0.02814**; **maximum reservation $0.05 USD**. No cache discounts assumed. Taxes/credit purchase fees excluded. Price uplift or context/budget mismatch causes refusal, not fallback or budget increase. The exclusive reservation remains spent for retry purposes after a timeout or error.

Environment: repository working directory; existing `OPENROUTER_API_KEY` found in environment or repository `.env` (value never printed). No paid account probe performed. Python's existing parser dependencies are available through the session-local `/tmp/credit-phase4-deps`; restore the repository requirements if that temporary directory is removed.

**Only after separate explicit authorization**, execute exactly once:

```sh
PYTHONPATH=/tmp/credit-phase4-deps ./scripts/credit-intelligence-fresh generate \
  --prepared artifacts/credit-rating-intelligence-v1/phase4/prepared \
  --authorize-payload-sha256 4eb1a99124a9a200f74416380d41ce8cba179032318d98627b2a6f64b2ccc802 \
  --max-cost-usd 0.05
```

The code verifies the original, canonical, catalog and payload before sending, persists one reservation, retains raw response/usage receipts, and refuses another attempt. Subsequent admission is offline and keeps new disclosures NEEDS_REVIEW until a separately pinned source review. Offline validation: **43 tests pass**, with both Phase 3 companies' exports byte-identical; synthetic results establish software behavior only. See [integration and review procedure](PHASE4.md).
