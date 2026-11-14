# Focus streaming

This is maintained source preparation. It does not install or activate a feed.
Use the independent [host supervisor](../../ops/market_live/README.md), whose
configuration binds identities/calendar, account quota path and activation dates.
The original acquisition owner is never held by a WebSocket.

The [official Upstox V3 specification](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/)
was checked on 14 November 2026. The client sends binary subscription messages
in `full` mode (LTPC alone has no minute OHLC), at most 50 equities and one
account-owned connection. This is below the documented normal full-mode limit
of 2,000 instruments and two connections per user. Other account consumers must
be inventoried before activation. No claim is made about sockets on other hosts.

`MarketDataFeed.proto` was downloaded from
`https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`.
SHA256: `3e1c939dce2c83a3fef91405c5ca268dfa089aae973319f56685f12a5afcf612`.
`protocol.py` decodes the actual Protobuf wire representation of the equity
FullFeed/LTPC/OHLC and market-status projection, skips unknown fields and rejects
malformed encodings and negative signed volumes. It needs no Protobuf runtime.
Original binary frames remain retained, including unprojected provider fields.
Revisit the projection and replay fixtures when upstream changes the schema.

Live transport uses the existing secure Upstox supplier, obtains a V3 authorized
URL without forwarding bearer credentials through HTTP redirects, validates its
WSS host and normalizes WebSocket disconnects. Install the optional dependency
in a dedicated reviewed environment:

```sh
python -m pip install --require-hashes -r scrapper/market_live/requirements.lock
```

`live-watchlist.json` is a separate explicit definition:

```json
{"schema_version":1,"version":"OWNER_CHOSEN_VERSION","company_ids":[]}
```

Replace the empty list with 20–50 actual positive company IDs. No historical
mentor/focus label becomes a subscription. Identity JSON supplies `observed_at`,
`source_ref`, `securities`, current `identifiers`, optional `scopes` and official
`provider_records`, using the existing HCD IdentityResolver fields. Every member
must resolve uniquely to a current NSE equity key with no conflicts. A changed
membership requires a new watchlist version; sub/unsub changes happen on the
open connection. Identity and resolved-list caching avoids rebuilding the full
exchange registry per message. Definition bytes and version are recorded in
status; original raw frames retain subscribed instrument keys.

Identity snapshots have a 24-hour maximum age. For a reviewed multi-day grant,
`identity_refresh_enabled: true` opts into at most one public official NSE
instrument-file request per IST day. The original registry snapshot stays
pinned. New compressed bytes and receipt timestamps are immutable overlays;
changed or conflicting symbols/ISINs block rather than replacing master IDs.
Failed/uncertain metadata delivery is retained and requires review. With this
option disabled, refresh the approved identity snapshot before it expires.
No credentials or provider requests were used to prepare these files.

`LiveStore` uses its own explicit live-state SQLite store and raw-frame directory.
It never opens or overwrites accepted historical candles. Each repeated minute
snapshot is selected by completion/event time, never added as new volume. Day
cumulative `vtt` and tick `ltq` are never used as candle volume. Signed per-minute
`vol` is summed once per selected minute. Late partial updates cannot replace
completed evidence; completed revisions rebuild dependent views. Receipt and
provider times stay distinct. REST reconciliation has no invented provider
broadcast timestamp: its completion bound is receipt time and its actual source
candle timestamp remains separate.

Completed 1-hour/4-hour bars use the existing session-boundary function, including
09:15 anchors, short closing buckets and split-session breaks. Daily bars require
all minutes in all reviewed session segments. Developing/GAP bars occupy a
separate chart channel and never become completed merely because the clock moved.
Missing minutes remain gaps. Intraday SMA/EMA/ATR/RVOL reuse maintained Decimal
engines, retain stored warmup, and reset across missing completed buckets; warmup
and unknown provider corporate-action basis are explicit.

A separate bounded REST worker repairs recent missing minutes using
[Intraday V3](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/)
for today and [Historical V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
for prior sessions. Each historical request spans one day, within the documented
one-month limit for minute intervals 1–15. Default recovery is five recent
calendar days and at most 50 requests per batch; maximums are 28 days/100 requests.
It shares the daily updater's account quota path, retains uncertain-delivery
stops and replays saved successful raw bytes without a second request. Older
unrepaired gaps remain visible. It runs alongside the stream, with independent
ownership and cooperative stop/expiry checks immediately before delivery.

`chart.html` reads the newest local `chart.js` every five seconds; it also accepts
a selected saved `chart.json`. It needs no network server or broker credentials.
Open the state directory's chart, not the source template. Reproduce the entire
20-instrument synthetic session in a new empty directory:

```sh
python3 -B scripts/market-live replay --output /tmp/live-fresh-new-replay
```

The result has seven hourly, two four-hour and one daily candle per instrument,
with exactly 37,500 volume despite duplicate/late partial snapshots. This is a
software replay, not proof of provider delivery or a live SLA. Illiquid stocks
may legitimately remain silent: the conservative per-instrument silence watchdog
reports STALE/reconnects, without claiming a proven provider outage. Receipt
storage/throughput and suitable silence thresholds require the authorized canary.
Retained frame history is not automatically deleted; size the approved volume.
