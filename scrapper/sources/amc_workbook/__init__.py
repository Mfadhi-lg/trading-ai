"""Safe local official-AMC workbook identification and inventory."""

from .detector import WorkbookInspection, WorkbookSheet, inspect_workbook

__all__ = ["WorkbookInspection", "WorkbookSheet", "inspect_workbook"]
