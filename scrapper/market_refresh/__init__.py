"""Bounded incremental market acquisition, separate from historical campaigns."""

from .planner import plan_refresh, coverage_report

__all__ = ['plan_refresh', 'coverage_report']
