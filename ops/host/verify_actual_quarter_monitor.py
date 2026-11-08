"""Invoke dependency-free existing quarter regressions in the pinned image.

The production image has no pytest runner. Compile the unchanged selected test
functions, which use ordinary assertions and no pytest fixtures or APIs.
"""
import ast
from pathlib import Path
import sqlite3
import tempfile
import time
from reporting_quarters import predicate


def main():
    selected={'test_period_filter_never_uses_publication_or_download_date',
        'test_quarter_render_never_substitutes_global_counts',
        'test_quarter_database_aggregation_and_realistic_backlog'}
    tree=ast.parse(Path(__file__).with_name('test_phase3_quarters.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in selected]
    scope={'sqlite3':sqlite3,'Path':Path,'predicate':predicate}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'existing-quarter-regressions','exec'),scope)
    for node in nodes:
        started=time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            args={'tmp_path':Path(tmp)} if node.args.args else {}
            scope[node.name](**args)
        print(node.name+': PASS '+str(round(time.monotonic()-started,3))+'s')
    if len(nodes)!=3:raise RuntimeError('Existing test function set changed')


if __name__=='__main__':main()
