# Private GitHub candidate publication

The existing private repository `Mfadhi-lg/trading-ai` receives review branch
`cleanup/c08.3-lifecycle-portability`, based on remote main `0ae886ae`.
This publishes the retained C06 + C08.1/C08.2/C08.3 candidate runtime and cleanup
documentation. Remote main predates these changes; the branch therefore includes
earlier acquisition and safety work as dependencies, not only the C08.3 patch.
The incremental C08.3 patch and exact offline evidence remain separately recorded.
No production deployment or main-branch merge is performed.

Credentials, .env files, stores, browser state/logs, generated zip/tar archives
and large retention archives are excluded from this publication. Historical
archive references remain local retention pointers with checksums; referenced
archives must be obtained from the operator's retained local copies. This GitHub
branch is a source review snapshot, not a corpus/backup/OCI archive.
All tested runtime bytes match the isolated candidate. A credential-pattern scan
of new staged files found no matching credentials; this is a bounded scan, not
a certification of every historic file. Historical source whitespace warnings
outside C08.3 are retained without unrelated edits.
