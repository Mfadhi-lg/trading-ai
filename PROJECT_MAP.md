# Where things are

This is a navigation map of the current checkout, not a proposed new structure.
Some folders contain both current work and older attempts, so check the specific
files below before moving or deleting anything.

## Technical analysis and candles

- `experiments/earnings_reaction_v1/svphd.py` — session volume profile work.
- `experiments/earnings_reaction_v1/candles.py` — candle models and validation.
- `experiments/earnings_reaction_v1/upstox_candles.py` — Upstox adapter.
- `experiments/earnings_reaction_v1/indicators.py`, `indicator_alternatives.py`,
  and `simple_volume.py` — indicator calculations used by the experiment.
- `experiments/earnings_reaction_v1/tests/` — tests for this work.
- `dtr_atr_rvol.pine` and `relative_strength.pine` — Pine scripts in the root.
- `artifacts/earnings-reaction-v1/` — inputs, results and reference material for
  these experiments. Some scripts and tests read it directly.

The experiment folder and Pine scripts are currently untracked by Git. SVP HD
is a reconstruction; TradingView parity and automatic resolution are unresolved.

## Scraper and acquisition

- `scraper` — the `./scraper` entry point. It passes commands to
  `ops/host/scraper_lifecycle.sh`.
- `ops/host/` — operator commands, lifecycle and monitoring. `wake` and
  `shutdown` lead to `acquisition_lifecycle.py` through
  `run_acquisition_lifecycle.py`.
- `ops/activation/automatic_acquisition.py` — scheduled host dispatcher.
  Other files here include its schedules and acquisition helpers, alongside
  historical rollout scripts.
- `scrapper/` — main Python package: sources, workflows, database, document
  processing, CLI and tests. The spelling `scrapper` is the actual package name.
- `artifacts/automatic-acquisition/` — dispatcher state, receipts and logs.
  This is operational state, not just disposable output.
- The production database is identified by the lifecycle code as Docker volume
  `trading-ai_scraper-data`; the database files under
  `artifacts/acquisition-readiness/` are historical snapshots, not necessarily
  the current database. The document store is configured separately.

## Document intelligence

- `scrapper/research/document_intelligence/` — document extraction and
  credit-intelligence code.
- `scrapper/research/general_ai_experiment/` — related model experiments.
- `scripts/credit-intelligence-*` — command entry points for some of this work.
- `artifacts/` contains outputs and retained inputs from multiple document
  experiments; it is not a single clean archive.

## Other folders

- `scripts/` — utility and command entry points, audits and checks; it is not
  the sole home of application code.
- `docs/` — documentation, but also large historical evidence archives under
  `docs/cleanup/baselines/`.
- `experiments/` — several separate exploratory projects, including active code.
- `artifacts/` — a mixture of outputs, snapshots, runtime state, inputs, and
  historical code copies. Some current code depends on exact paths inside it.
- `github-source-candidate-20261023/` — a separate historical source checkout,
  not the main package.
- `OLD/` — historical material; the root README says some inputs are still used.

## How to simplify safely

First preserve the current untracked work and decide what staged files belong in
version control. Then map active dependencies and extract one area at a time:
technical analysis, acquisition, and document intelligence. Separate runtime
state and valuable inputs from disposable outputs before relocating artifacts.
Moving a folder without updating its consumers can break commands or tests.
