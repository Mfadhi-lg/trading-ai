"""Identify XLS/XLSX bytes and inventory XLSX sheets without evaluating cells."""

from __future__ import annotations

import hashlib
import json
import posixpath
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

import xlrd

from ...config import (
    MF_MAX_FILE_BYTES,
    MF_MAX_XML_MEMBER_BYTES,
    MF_MAX_ZIP_COMPRESSION_RATIO,
    MF_MAX_ZIP_ENTRIES,
    MF_MAX_ZIP_MEMBER_BYTES,
    MF_MAX_ZIP_UNCOMPRESSED_BYTES,
)

XLS_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
CONTENT_TYPES = "[Content_Types].xml"
WORKBOOK_XML = "xl/workbook.xml"
WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"
SHEET_REL_TYPE = "/worksheet"


@dataclass(frozen=True)
class WorkbookSheet:
    position: int
    name: str
    visibility_state: str
    package_part: str


@dataclass(frozen=True)
class WorkbookInspection:
    path: str
    original_filename: str
    content_sha256: str
    byte_size: int
    extension: str
    workbook_type: str
    proposed_state: str
    decision_code: str
    reason: str
    safety_status: str
    inventory_status: str
    sheets: tuple[WorkbookSheet, ...]
    safety_checks: tuple[str, ...]

    @property
    def is_safe(self) -> bool:
        return self.proposed_state == "inbox"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["sheets"] = [asdict(sheet) for sheet in self.sheets]
        payload["safety_checks"] = list(self.safety_checks)
        return payload


class UnsafeWorkbook(ValueError):
    """A deterministic safety or package-integrity rejection."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quarantined(
    path: Path,
    sha256: str,
    byte_size: int,
    extension: str,
    workbook_type: str,
    code: str,
    reason: str,
    checks: tuple[str, ...],
) -> WorkbookInspection:
    return WorkbookInspection(
        path=str(path.resolve()),
        original_filename=path.name,
        content_sha256=sha256,
        byte_size=byte_size,
        extension=extension,
        workbook_type=workbook_type,
        proposed_state="quarantine",
        decision_code=code,
        reason=reason,
        safety_status="quarantined",
        inventory_status="not_started",
        sheets=(),
        safety_checks=checks,
    )


def _safe_xml(payload: bytes, member: str) -> ElementTree.Element:
    if len(payload) > MF_MAX_XML_MEMBER_BYTES:
        raise UnsafeWorkbook("xml_member_too_large", f"XML member is too large: {member}")
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise UnsafeWorkbook(
            "xml_document_type_forbidden",
            f"DTD/entity declarations are forbidden in workbook XML: {member}",
        )
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise UnsafeWorkbook("malformed_package_xml", f"Malformed XML in {member}: {exc}") from exc


def _validate_member_name(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if name.startswith("/") or ".." in parts or not name:
        raise UnsafeWorkbook("unsafe_zip_path", f"Unsafe ZIP member path: {info.filename}")
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == 0o120000:
        raise UnsafeWorkbook("zip_symlink_forbidden", f"ZIP symlink is forbidden: {name}")
    if info.flag_bits & 0x1:
        raise UnsafeWorkbook("encrypted_zip_member", f"Encrypted ZIP member is forbidden: {name}")


def _validate_zip_limits(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MF_MAX_ZIP_ENTRIES:
        raise UnsafeWorkbook("zip_entry_limit", "Workbook ZIP contains too many entries")
    if len({info.filename for info in infos}) != len(infos):
        raise UnsafeWorkbook("duplicate_zip_member", "Workbook ZIP contains duplicate member names")
    total = 0
    for info in infos:
        _validate_member_name(info)
        if info.file_size > MF_MAX_ZIP_MEMBER_BYTES:
            raise UnsafeWorkbook("zip_member_size_limit", f"ZIP member is too large: {info.filename}")
        total += info.file_size
        compressed = max(info.compress_size, 1)
        if info.file_size > 1024 and info.file_size / compressed > MF_MAX_ZIP_COMPRESSION_RATIO:
            raise UnsafeWorkbook(
                "zip_compression_ratio_limit",
                f"ZIP member compression ratio is unsafe: {info.filename}",
            )
    if total > MF_MAX_ZIP_UNCOMPRESSED_BYTES:
        raise UnsafeWorkbook("zip_uncompressed_size_limit", "Workbook expands beyond the safety limit")
    return infos


def _relationship_target(base_part: str, target: str) -> str:
    if target.startswith("/"):
        resolved = target.lstrip("/")
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))
    if resolved.startswith("../") or resolved == "..":
        raise UnsafeWorkbook("unsafe_relationship_target", f"Unsafe package relationship: {target}")
    return resolved


def _inventory_xlsx(path: Path) -> tuple[tuple[WorkbookSheet, ...], tuple[str, ...]]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsafeWorkbook("malformed_xlsx_zip", f"Invalid XLSX ZIP package: {exc}") from exc
    with archive:
        infos = _validate_zip_limits(archive)
        names = {info.filename for info in infos}
        required = {CONTENT_TYPES, WORKBOOK_XML, WORKBOOK_RELS}
        missing = sorted(required - names)
        if missing:
            raise UnsafeWorkbook("missing_xlsx_part", f"Required XLSX parts are missing: {missing}")
        lower_names = {name.lower() for name in names}
        if any("vbaproject" in name or name.startswith("xl/macrosheets/") for name in lower_names):
            raise UnsafeWorkbook("macro_content_forbidden", "Macro-bearing workbook content is forbidden")
        if any(name.startswith("xl/externallinks/") for name in lower_names):
            raise UnsafeWorkbook("external_links_forbidden", "External workbook links are forbidden")

        content_types = _safe_xml(archive.read(CONTENT_TYPES), CONTENT_TYPES)
        for element in content_types.iter():
            content_type = element.attrib.get("ContentType", "").lower()
            if "macroenabled" in content_type or "vbaproject" in content_type:
                raise UnsafeWorkbook("macro_content_forbidden", "Macro-enabled content type is forbidden")

        relationships: dict[str, str] = {}
        for rel_name in sorted(name for name in names if name.endswith(".rels")):
            root = _safe_xml(archive.read(rel_name), rel_name)
            for relation in root:
                if relation.attrib.get("TargetMode", "").lower() == "external":
                    raise UnsafeWorkbook(
                        "external_relationship_forbidden",
                        f"External relationship is forbidden in {rel_name}",
                    )
                if rel_name == WORKBOOK_RELS:
                    rel_id = relation.attrib.get("Id")
                    rel_type = relation.attrib.get("Type", "")
                    target = relation.attrib.get("Target")
                    if rel_id and target and rel_type.endswith(SHEET_REL_TYPE):
                        relationships[rel_id] = _relationship_target(WORKBOOK_XML, target)

        workbook = _safe_xml(archive.read(WORKBOOK_XML), WORKBOOK_XML)
        sheets_parent = next((node for node in workbook.iter() if node.tag.endswith("}sheets")), None)
        if sheets_parent is None:
            raise UnsafeWorkbook("workbook_has_no_sheets", "Workbook has no sheets collection")
        sheets: list[WorkbookSheet] = []
        for position, node in enumerate(list(sheets_parent)):
            name = node.attrib.get("name")
            rel_id = next((value for key, value in node.attrib.items() if key.endswith("}id")), None)
            state = node.attrib.get("state", "visible")
            if not name or not rel_id or rel_id not in relationships:
                raise UnsafeWorkbook("invalid_sheet_relationship", "Sheet name or package relationship is missing")
            part = relationships[rel_id]
            if part not in names:
                raise UnsafeWorkbook("missing_sheet_part", f"Sheet package part is missing: {part}")
            if state not in {"visible", "hidden", "veryHidden"}:
                state = "unknown"
            sheets.append(WorkbookSheet(position, name, state, part))
        if not sheets:
            raise UnsafeWorkbook("workbook_has_no_sheets", "Workbook contains no sheets")
        bad_crc_member = archive.testzip()
        if bad_crc_member:
            raise UnsafeWorkbook("zip_crc_failure", f"ZIP member failed CRC validation: {bad_crc_member}")
    return tuple(sheets), (
        "signature_and_extension_match",
        "zip_limits_passed",
        "no_encrypted_or_unsafe_members",
        "no_macros_or_external_relationships",
        "package_relationships_complete",
        "crc_passed",
        "formulas_not_loaded_or_evaluated",
    )


def _valid_xls_header(path: Path) -> bool:
    with path.open("rb") as source:
        header = source.read(512)
    if len(header) < 512 or not header.startswith(XLS_MAGIC):
        return False
    major_version = int.from_bytes(header[26:28], "little")
    byte_order = header[28:30]
    sector_shift = int.from_bytes(header[30:32], "little")
    return byte_order == b"\xfe\xff" and (major_version, sector_shift) in {(3, 9), (4, 12)}


def _inventory_xls(path: Path) -> tuple[tuple[WorkbookSheet, ...], tuple[str, ...]]:
    try:
        book = xlrd.open_workbook(
            path,
            formatting_info=False,
            on_demand=True,
            use_mmap=False,
            ignore_workbook_corruption=False,
        )
    except (OSError, xlrd.XLRDError) as exc:
        raise UnsafeWorkbook("malformed_xls_stream", f"Invalid legacy XLS: {exc}") from exc
    try:
        visibility_names = {0: "visible", 1: "hidden", 2: "veryHidden"}
        sheets = tuple(
            WorkbookSheet(
                position,
                name,
                visibility_names.get(book._sheet_visibility[position], "unknown"),
                f"Workbook/Sheet{position + 1}",
            )
            for position, name in enumerate(book.sheet_names())
        )
        if not sheets:
            raise UnsafeWorkbook("workbook_has_no_sheets", "Workbook has no sheets")
        return sheets, (
            "signature_and_extension_match",
            "compound_document_parsed_without_execution",
            "macros_and_embedded_objects_ignored",
            "external_links_not_followed",
            "formulas_not_evaluated",
        )
    finally:
        book.release_resources()


def inspect_workbook(
    path: Path,
    *,
    max_file_bytes: int = MF_MAX_FILE_BYTES,
    allow_ooxml_xls_extension: bool = False,
    allow_legacy_xls_parse: bool = False,
) -> WorkbookInspection:
    """Hash and safely identify one file. This function performs no writes."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Symbolic-link workbook inputs are not allowed: {path}")
    if not path.is_file():
        raise ValueError(f"Workbook input is not a regular file: {path}")
    byte_size = path.stat().st_size
    sha256 = _sha256(path)
    extension = path.suffix.lower()
    with path.open("rb") as source:
        magic = source.read(8)
    if byte_size > max_file_bytes:
        return _quarantined(
            path, sha256, byte_size, extension, "unknown", "file_size_limit",
            f"Workbook exceeds the {max_file_bytes}-byte input limit", (),
        )

    if magic.startswith(ZIP_MAGICS):
        workbook_type = "xlsx"
        extension_allowed = extension == ".xlsx" or (
            allow_ooxml_xls_extension and extension == ".xls"
        )
        if not extension_allowed:
            return _quarantined(
                path, sha256, byte_size, extension, workbook_type,
                "extension_signature_mismatch", "XLSX content must use the .xlsx extension", (),
            )
        try:
            sheets, checks = _inventory_xlsx(path)
        except UnsafeWorkbook as exc:
            return _quarantined(
                path, sha256, byte_size, extension, workbook_type, exc.code, str(exc), (),
            )
        if extension == ".xls":
            checks = (*checks, "publisher_manifest_allows_ooxml_xls_extension")
        return WorkbookInspection(
            str(path.resolve()), path.name, sha256, byte_size, extension, workbook_type,
            "inbox", "inventory", "Safe XLSX package inventoried", "passed",
            "sheet_metadata_complete", sheets, checks,
        )

    if magic == XLS_MAGIC:
        workbook_type = "xls"
        if extension != ".xls":
            return _quarantined(
                path, sha256, byte_size, extension, workbook_type,
                "extension_signature_mismatch", "XLS content must use the .xls extension", (),
            )
        if not _valid_xls_header(path):
            return _quarantined(
                path, sha256, byte_size, extension, workbook_type,
                "malformed_xls_header", "Legacy XLS compound-file header is malformed", (),
            )
        if allow_legacy_xls_parse:
            try:
                sheets, checks = _inventory_xls(path)
            except UnsafeWorkbook as exc:
                return _quarantined(
                    path, sha256, byte_size, extension, workbook_type,
                    exc.code, str(exc), (),
                )
            return WorkbookInspection(
                str(path.resolve()), path.name, sha256, byte_size, extension,
                workbook_type, "inbox", "inventory",
                "Legacy XLS sheet inventory parsed without executing workbook content",
                "passed", "sheet_metadata_complete", sheets, checks,
            )
        return WorkbookInspection(
            str(path.resolve()), path.name, sha256, byte_size, extension, workbook_type,
            "inbox", "inventory", "Legacy XLS identified; package contents were not executed or evaluated",
            "legacy_binary_not_evaluated", "deferred_legacy_binary", (),
            ("signature_and_extension_match", "macros_not_executed", "external_links_not_evaluated"),
        )

    return _quarantined(
        path, sha256, byte_size, extension, "unknown", "unsupported_or_invalid_signature",
        "File bytes do not have a supported XLS or XLSX signature", (),
    )


def safety_report_json(inspection: WorkbookInspection) -> str:
    return json.dumps(
        {
            "decision_code": inspection.decision_code,
            "reason": inspection.reason,
            "inventory_status": inspection.inventory_status,
            "checks": list(inspection.safety_checks),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
