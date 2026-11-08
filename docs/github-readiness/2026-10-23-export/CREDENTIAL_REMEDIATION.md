# Historical credential: restricted owner action

The original Git commit `e71d1ee168d4a2b5c155aa6302347aeaad841e46` contains a nonempty 32-byte Screener CSRF token in `.screener_csrf`. The commit is reachable from the original HEAD and two recorded local branch refs. Removal from the current tree did not remove historical exposure. Local reconfirmation shows that the historical token still equals the locally stored CSRF token. No secret values or credential digests are recorded here.

This is anti-forgery material. It is not by itself evidence that an authenticated session is usable. A separate local session file exists; server-side token/session validity, account identity, prior uploads and remote visibility remain unknown. No login, HTTP validity request, remote query or provider operation was performed. Flag the token as **potentially active and exposed**, without claiming confirmed active authentication.

Before publication approval, the authorized account owner must coordinate invalidation/renewal of the applicable CSRF material and review/invalidate related authenticated sessions as appropriate. If a session replacement is required, provision it outside Git through the established secret-file mechanism and validate acquisition through a separately authorized maintenance process. Record completion metadata only, never values, screenshots of credentials or credential hashes.

The owner should establish whether the original history was uploaded or shared and identify recipients/retained copies using separately authorized remote review. If remote copies contain the credential, their remediation requires separate authorization. Fresh export history prevents carrying this old object into the candidate; it does not revoke credentials or remediate existing copies.

**No credential was rotated, revoked, copied into the candidate or transmitted during this task.** See `credential-investigation.json` and `credential-reconfirmation.json`. Do not push the original repository or its refs.
