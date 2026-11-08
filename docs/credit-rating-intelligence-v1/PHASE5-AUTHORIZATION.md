# One full-rationale request — authorization pending

This command is prepared for a future explicit authorization. It has **not** been executed.

- Company/source: Aether Industries Limited, nine-page ICRA rationale dated 26 May 2026.
- Model/provider/reasoning: Gemini 3.8 Flash / Google AI Studio Flex / low.
- Exact payload SHA-256: `f04870df1ac2416a22fa54025d940e54376db2b6d5fb97b31f40e0f8a763f59b`.
- Expected cost: approximately $0.076; conservative bound $0.258350625; maximum reservation $0.35.
- Output limit: 65,536 tokens including reasoning. No tools, search, retry, fallback, Batch or additional company.

From the repository root, after separate authorization:

```sh
PYTHONPATH=/tmp/credit-phase4-deps ./scripts/credit-intelligence-fresh generate \
  --prepared artifacts/credit-rating-intelligence-v1/phase5/prepared \
  --authorize-payload-sha256 f04870df1ac2416a22fa54025d940e54376db2b6d5fb97b31f40e0f8a763f59b \
  --max-cost-usd 0.35
```

Preparation is verified before send. The one-attempt reservation is exclusive and survives errors/timeouts. No second send from this preparation is permitted. If the temporary Phase 4 dependency directory no longer exists, restore the same dependencies before offline verification; do not change source bytes, silently reprepare, alter the payload or issue a substitute request. Read [PHASE5-SOURCE-REVIEW.md](PHASE5-SOURCE-REVIEW.md) before admission.
