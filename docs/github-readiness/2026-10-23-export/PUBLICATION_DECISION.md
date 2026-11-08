# Private GitHub publication decision

**Decision: BLOCKED — retain the reviewed candidate locally.**

The source-only packaging and bounded offline checks pass. The candidate has independent history, an exact manifest, complete original-to-export mapping, no configured remote and no confirmed exported credential. These results are sufficient for local review, but do not establish full runtime reproducibility or permission to share.

The following gates remain:

1. **Credential remediation:** the exposed historical CSRF token still matches local material. Owner-authorized invalidation/renewal and associated session review remain outstanding; actual server validity and remote exposure are unknown. Do not rotate it as part of this export task.
2. **Rights and ownership:** obtain owner clearance for source and third-party metadata/public CA material and any retained test literals. No project license or general publisher-data redistribution permission has been established. Private visibility does not supply those permissions.
3. **Incomplete runtime/test dependencies:** required Focus historical inputs, exchange reference observations, operational release/authorization controls and some historical test fixtures are deliberately external. Preserve them locally; provide a reviewed external-data contract or safe synthetic replacements before claiming affected workflows or the complete suite work from a clone. Five optional research imports still need dependency locks/platform setup.
4. **Machine assumptions:** retained adapter/runtime identity paths and safety controls require configuration review before use elsewhere. Do not weaken production guards or install schedulers to make an export test pass.
5. **Snapshot acceptance:** four selected original source files have changed during preparation. Review the frozen mapping and explicitly accept its snapshot, or prepare and reverify a subsequent candidate that incorporates selected changes. Do not silently synchronize the live tree.

No GitHub repository was created or queried, no remote was added, and nothing was pushed. A future publication step must use only the reviewed independent candidate, private visibility and explicit authorization. Do not publish this local evidence package, the workspace index or original history. This decision does not authorize any remote operation.
