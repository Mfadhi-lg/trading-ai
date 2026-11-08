"""Compatible Markdown rendering for retry diagnosis reports."""

from __future__ import annotations

def render_markdown(d: dict) -> str:
    out: list[str] = []
    a = out.append
    a("# RETRYABLE_FAILURE diagnosis\n")
    a(f"- Generated: `{d['generated_at_utc']}`")
    a(f"- DB: `{d['db_path']}`")
    a(f"- Scope: `{d['scope_id'] or 'ALL'}`")
    a(f"- Total retryable: **{d['total_retryable']:,}**")
    a(f"- Due now: **{d['due_now']:,}** / scheduled in future: {d['scheduled_future']:,}")
    a(f"- Never attempted (attempt_count = 0): **{d['never_attempted']:,}**")
    a(f"- Attempted at least once: **{d['attempted_at_least_once']:,}**\n")

    v = d["verdict"]
    a("## Verdict (§6 decision rule)\n")
    a(f"- Classification: **{v['classification']}**")
    a(f"- Code change required: **{v['code_change_required']}**")
    a(f"- Action: {v['action']}\n")
    for reason in v["reasons"]:
        a(f"  - {reason}")
    a("")

    a("## By failure family\n")
    a("| family | jobs | of which actually attempted |")
    a("| --- | ---: | ---: |")
    for fam, n in d["by_failure_family"].items():
        a(f"| {fam} | {n:,} | {d['by_failure_family_attempted_only'].get(fam, 0):,} |")

    a("\n## By attempt count\n")
    a("| attempt_count | jobs |")
    a("| --- | ---: |")
    for k, v in d["by_attempt_count"].items():
        a(f"| {k} | {v:,} |")

    a("\n## By artifact type\n")
    a("| artifact type | jobs |")
    a("| --- | ---: |")
    for k, v in d["by_artifact_type"].items():
        a(f"| {k} | {v:,} |")

    a("\n## Top hosts\n")
    a("| host | retryable | circuit open now | validated / attempts | latest success | dominant family |")
    a("| --- | ---: | --- | --- | --- | --- |")
    for host, det in list(d["host_detail"].items())[:25]:
        fam = next(iter(det["families"]), "")
        a(
            f"| {host} | {det['retryable_jobs']:,} | {det['circuit_open_now']} | "
            f"{det['validated_all_time']:,} / {det['attempts_all_time']:,} | "
            f"{det['latest_success_utc'] or '-'} | {fam} |"
        )
    return "\n".join(out) + "\n"

