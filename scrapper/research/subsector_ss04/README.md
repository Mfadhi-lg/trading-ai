# SS04 evidence-conservative revision

Run `python3 -m scrapper.research.subsector_ss04.build` to reproduce CSV exports.
The existing SS01 research database is copied using SQLite backup on first run.
Only additive ss04 tables and the existing SS03 canonical overlay are applied to
`artifacts/subsector-ss04/research.sqlite3`. No production database is opened.

The major population is the existing SS02 retained `eligible` scope, deduplicated
by canonical company ID. No new master or security universe is constructed.
All 4,428 retained company records are traversed in historical, Q2, remaining order.
This is a conservative processing pass, **not completed primary-source research**.
Unrecognized activities and ambiguous primary activities remain explicit gaps.
Do not interpret a reason generated from retained metadata as proof that all
public sources have been exhausted.

The migration adds revision decisions and exposure rows; original SS01/02 facts
are immutable. `rollback(connection)` drops only ss04 tables. Reapplying after
rollback reproduces identical decisions; tests use a disposable backup. Existing
SS03 normalization remains part of the baseline. An existing revision is frozen;
code changes require rollback/reapply on this isolated artifact or a new version.

Undated issuer webpages inspected on 2026-11-02 support retrospective business
classification only. Original source publication dates, unknown availability,
retained observation IDs, primary citation links and source-row locators are in
the evidence export. No tutor assessment or historical source is edited.
