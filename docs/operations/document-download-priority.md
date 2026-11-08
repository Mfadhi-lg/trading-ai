# Document download priority and large files

Automatic downloads (standalone batches and financial companions) use the same
current-quarter-first selection when `historical_download_fallback` is enabled.
Current-quarter documents take all available batch slots first, including jobs
created before the acquisition epoch. Remaining slots go to post-epoch documents
from other or unknown periods, then pre-epoch historical work. Historical work
can fill the entire batch when no higher-priority work is ready.

Company eligibility, publisher policy, retry due dates, host cooldowns, existing
content deduplication and ownership fences still apply. Discovery and processing
retain their existing scheduling rules. Newly downloaded historical files have
fresh content timestamps and enter the normal processing inbox.

`download_max_response_bytes` controls document downloads separately from page
and rating response limits. It is now 256 MiB (previously 10 MiB), with a
120-second per-download deadline. The response is still buffered in memory, so a
finite file limit protects the worker. This covers the 60 September-quarter size
failures observed on 8 November 2026; their largest recorded file was 30,153,608
bytes. Those exact jobs were requeued through the audited recovery repository,
retaining previous attempts and recording the operator authorization.

Download byte reservations and physical free-space checks use the larger limit
for both standalone batches and financial companions. Daily request and document
batch counts remain unchanged.

Validation: `test_download_history_fallback`, `test_phase4_routine`,
`test_phase4_document_selection`, `test_d02_document_downloader`, and
`test_automatic_rating_browser`; 71 tests, one skipped. Both existing throughput
capacity tests also pass with the enlarged byte reservations.
