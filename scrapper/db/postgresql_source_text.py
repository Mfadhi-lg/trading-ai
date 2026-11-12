"""Lossless UTF-8 storage for explicitly mapped, unfiltered document source text.

SQLite TEXT may contain NUL; PostgreSQL TEXT cannot. These native BYTEA columns
retain UTF-8 bytes, while repository payloads and transfer digests retain str.
"""
from .repository_backend import is_postgresql

SOURCE_TEXT_COLUMNS = {
    "credit_rating_passages": frozenset({"text_raw"}),
    "document_page_elements": frozenset({"text_raw"}),
    "document_pages": frozenset({"title_raw", "native_text", "ocr_text", "selected_text"}),
}


def encode_source_text(value):
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("Source text must be str or None")
    return value.encode("utf-8", errors="strict")


def decode_source_text(value):
    if value is None or type(value) is str:
        return value
    if type(value) is not bytes:
        raise TypeError("Native source text must be bytes")
    return value.decode("utf-8", errors="strict")


def bind_source_text(conn, value):
    return encode_source_text(value) if is_postgresql(conn) else value


def transfer_row(table, columns, row, *, to_postgresql):
    mapped = SOURCE_TEXT_COLUMNS.get(table, ())
    if not mapped:
        return row
    convert = encode_source_text if to_postgresql else decode_source_text
    return tuple(convert(value) if col[1] in mapped else value
                 for col, value in zip(columns, row))
