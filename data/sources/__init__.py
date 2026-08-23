"""Data source connectors.

Public API — import these from `data.sources` (or via the legacy
`data.connector` shim which re-exports the same names):

    DataSource              — abstract base class
    ExcelDataSource         — .xlsx / .xls via calamine (Rust) → DuckDB
    CSVDataSource           — .csv via DuckDB read_csv_auto
    SQLDataSource           — any SQLAlchemy-supported DB + DuckDB analysis cache
    GoogleSheetsDataSource  — service-account JSON → all worksheets
    HTTPAPIDataSource       — JSON/CSV REST endpoint → DataFrame
    MAX_DISPLAY_ROWS        — preview row cap surfaced to the LLM
"""
from .base    import DataSource, MAX_DISPLAY_ROWS
from .excel   import ExcelDataSource
from .csv     import CSVDataSource
from .sql     import SQLDataSource
from .gsheets import GoogleSheetsDataSource
from .http    import HTTPAPIDataSource
from .feishu_bitable import FeishuBitableDataSource

__all__ = [
    "DataSource",
    "MAX_DISPLAY_ROWS",
    "ExcelDataSource",
    "CSVDataSource",
    "SQLDataSource",
    "GoogleSheetsDataSource",
    "HTTPAPIDataSource",
    "FeishuBitableDataSource",
]
