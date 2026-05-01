"""
Library:     lib_bejson_core.py
Jurisdiction: ["PYTHON", "CORE_COMMAND"]
Status:      OFFICIAL — Core-Command/Lib (v1.1)
Author:      Elton Boehnen
Version:     1.1 (OFFICIAL) Admin fork
Date:        2026-04-23
Description: BEJSON core library — document creation, mutation, validation,
             atomic file I/O with fsync, and query/sort utilities.
             MFDB relational functions are in lib_mfdb_core.py (decoupled).
"""
import time

import copy
import json
import os
import sys
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from lib_bejson_validator import (
    BEJSONValidationError,
    bejson_validator_get_report,
    bejson_validator_validate_file,
    bejson_validator_validate_string,
)

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

E_CORE_INVALID_VERSION = 20
E_CORE_INVALID_OPERATION = 21
E_CORE_INDEX_OUT_OF_BOUNDS = 22
E_CORE_FIELD_NOT_FOUND = 23
E_CORE_TYPE_CONVERSION_FAILED = 24
E_CORE_BACKUP_FAILED = 25
E_CORE_WRITE_FAILED = 26
E_CORE_QUERY_FAILED = 27


class BEJSONCoreError(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# ATOMIC FILE OPERATIONS
# ---------------------------------------------------------------------------

def __bejson_core_atomic_backup(file_path: str, backup_suffix: str = ".backup") -> str:
    """
    Create a timestamped backup of file_path.
    Returns the backup path, or '' if no file existed.
    Mirrors __bejson_core_atomic_backup.
    """
    path = Path(file_path)
    if not path.exists():
        return ""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}{backup_suffix}.{timestamp}")
    try:
        shutil.copy2(path, backup_path)
    except OSError as exc:
        raise BEJSONCoreError(f"Backup failed: {exc}", E_CORE_BACKUP_FAILED)
    return str(backup_path)


def __bejson_core_restore_backup(file_path: str, backup_path: str) -> bool:
    """
    Restore backup_path → file_path.
    Mirrors __bejson_core_restore_backup.
    """
    bp = Path(backup_path)
    if bp.exists():
        shutil.move(str(bp), file_path)
        return True
    return False


def bejson_core_atomic_write(file_path: str, content: dict, create_backup: bool = True) -> None:
    """
    Validate content and write it atomically to file_path.
    CRITICAL FIXES (v3.1):
      - Same-partition temp files: temp file is created as a sibling to the
        target file (same directory), guaranteeing atomic os.rename() on the
        same filesystem.  Cross-device falls back to shutil.copy2.
      - Explicit fsync: file descriptor is flushed via os.fsync() BEFORE
        rename, closing the data-loss window from volatile page cache.
      - Cross-filesystem safe: if os.rename fails (EXDEV), falls back to
        shutil.copy2 + os.unlink (atomic enough with fsync).
    Mirrors bejson_core_atomic_write.
    """
    backup_path = ""
    if create_backup:
        backup_path = __bejson_core_atomic_backup(file_path)

    json_text = json.dumps(content, indent=2)

    # Write to a temp file first, validate, then rename
    path = Path(file_path)
    # Ensure output parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # CRITICAL FIX: always create temp as a SIBLING to the target file
    # so os.rename stays on the same partition (true atomic inode swap).
    temp_dir = str(path.parent)
    # Only fall back to system tmpdir if parent is not writable AND
    # the user explicitly set TMPDIR (opt-in cross-partition fallback).
    if not os.access(temp_dir, os.W_OK):
        user_tmp = os.environ.get("TMPDIR", "")
        if user_tmp:
            temp_dir = user_tmp
            os.makedirs(temp_dir, exist_ok=True)
        else:
            raise BEJSONCoreError(
                f"Cannot write to target directory '{temp_dir}' and "
                "TMPDIR not set. Set TMPDIR to a writable location on the "
                "same partition as the target, or grant write permission.",
                E_CORE_WRITE_FAILED,
            )

    tmp_fd = None
    tmp_path = ""
    try:
        # Use O_WRONLY|O_CREAT|O_EXCL for safe temp file creation
        fd, tmp_path = tempfile.mkstemp(
            dir=temp_dir, suffix=".tmp", prefix=".bejson_"
        )
        tmp_fd = fd
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(json_text)
            # CRITICAL FIX: explicit fsync before close
            tmp.flush()
            os.fsync(tmp.fileno())
    except OSError as exc:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if backup_path:
            __bejson_core_restore_backup(file_path, backup_path)
        raise BEJSONCoreError(f"Write failed: {exc}", E_CORE_WRITE_FAILED)

    # Validate the temp file BEFORE committing
    try:
        bejson_validator_validate_file(tmp_path)
    except BEJSONValidationError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if backup_path:
            __bejson_core_restore_backup(file_path, backup_path)
        raise BEJSONCoreError(f"Validation failed: {exc}", E_CORE_WRITE_FAILED)

    # Atomic rename (same-partition guaranteed by sibling temp)
    try:
        os.rename(tmp_path, file_path)
        # fsync the directory entry to ensure rename is durable
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        # Cross-filesystem: fall back to copy2 + unlink (still safe with fsync)
        try:
            shutil.copy2(tmp_path, file_path)
            os.unlink(tmp_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            if backup_path:
                __bejson_core_restore_backup(file_path, backup_path)
            raise BEJSONCoreError(f"Atomic move failed: {exc}", E_CORE_WRITE_FAILED)

    # Clean up old backup (write succeeded)
    if backup_path:
        Path(backup_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# DOCUMENT CREATION
# ---------------------------------------------------------------------------

def bejson_core_create_104(records_type: str, fields: list[dict], values: list[list]) -> dict:
    """
    Create a BEJSON 104 document.
    Mirrors bejson_core_create_104.
    """
    return {
        "Format": "BEJSON",
        "Format_Version": "104",
        "Format_Creator": "Elton Boehnen",
        "Records_Type": [records_type],
        "Fields": fields,
        "Values": values,
    }


def bejson_core_create_104a(
    records_type: str,
    fields: list[dict],
    values: list[list],
    **custom_headers,
) -> dict:
    """
    Create a BEJSON 104a document with optional custom top-level headers.
    Mirrors bejson_core_create_104a.
    """
    doc = {
        "Format": "BEJSON",
        "Format_Version": "104a",
        "Format_Creator": "Elton Boehnen",
        "Records_Type": [records_type],
        "Fields": fields,
        "Values": values,
    }
    doc.update(custom_headers)
    return doc


def bejson_core_create_104db(
    records_types: list[str], fields: list[dict], values: list[list]
) -> dict:
    """
    Create a BEJSON 104db document (multi-type).
    Mirrors bejson_core_create_104db.
    """
    return {
        "Format": "BEJSON",
        "Format_Version": "104db",
        "Format_Creator": "Elton Boehnen",
        "Records_Type": records_types,
        "Fields": fields,
        "Values": values,
    }


# ---------------------------------------------------------------------------
# DOCUMENT LOADING & PARSING
# ---------------------------------------------------------------------------

def bejson_core_load_file(file_path: str) -> dict:
    """
    Load and validate a BEJSON file from disk.
    Mirrors bejson_core_load_file.
    """
    path = Path(file_path)
    if not path.exists():
        raise BEJSONCoreError(f"File not found: {file_path}", E_CORE_FIELD_NOT_FOUND)
    bejson_validator_validate_file(file_path)
    return json.loads(path.read_text(encoding="utf-8"))


def bejson_core_load_string(json_string: str) -> dict:
    """
    Parse and validate a BEJSON JSON string.
    Mirrors bejson_core_load_string.
    """
    bejson_validator_validate_string(json_string)
    return json.loads(json_string)


def bejson_core_get_version(doc: dict) -> str:
    """Return the Format_Version. Mirrors bejson_core_get_version."""
    return doc["Format_Version"]


def bejson_core_get_records_types(doc: dict) -> list[str]:
    """Return the Records_Type list. Mirrors bejson_core_get_records_types."""
    return doc["Records_Type"]


def bejson_core_get_fields(doc: dict) -> list[dict]:
    """Return the Fields list. Mirrors bejson_core_get_fields."""
    return doc["Fields"]


def bejson_core_get_field_index(doc: dict, field_name: str) -> int:
    """
    Return the zero-based index of a field by name.
    Raises BEJSONCoreError if not found.
    Mirrors bejson_core_get_field_index.
    """
    for i, f in enumerate(doc["Fields"]):
        if f["name"] == field_name:
            return i
    raise BEJSONCoreError(f"Field not found: {field_name}", E_CORE_FIELD_NOT_FOUND)


def bejson_core_get_field_def(doc: dict, field_name: str) -> dict:
    """
    Return the field definition dict for a named field.
    Mirrors bejson_core_get_field_def.
    """
    for f in doc["Fields"]:
        if f["name"] == field_name:
            return f
    raise BEJSONCoreError(f"Field not found: {field_name}", E_CORE_FIELD_NOT_FOUND)


def bejson_core_get_field_count(doc: dict) -> int:
    """Return number of fields. Mirrors bejson_core_get_field_count."""
    return len(doc["Fields"])


def bejson_core_get_record_count(doc: dict) -> int:
    """Return number of records. Mirrors bejson_core_get_record_count."""
    return len(doc["Values"])


# ---------------------------------------------------------------------------
# POSITION-BASED INDEXING & QUERYING
# ---------------------------------------------------------------------------

def _check_record_bounds(doc: dict, record_index: int):
    if record_index < 0 or record_index >= bejson_core_get_record_count(doc):
        raise BEJSONCoreError(
            f"Record index {record_index} out of bounds", E_CORE_INDEX_OUT_OF_BOUNDS
        )


def _check_field_bounds(doc: dict, field_index: int):
    if field_index < 0 or field_index >= bejson_core_get_field_count(doc):
        raise BEJSONCoreError(
            f"Field index {field_index} out of bounds", E_CORE_INDEX_OUT_OF_BOUNDS
        )


def bejson_core_get_value_at(doc: dict, record_index: int, field_index: int) -> Any:
    """
    Return the value at [record_index][field_index].
    Mirrors bejson_core_get_value_at.
    """
    _check_record_bounds(doc, record_index)
    _check_field_bounds(doc, field_index)
    return doc["Values"][record_index][field_index]


def bejson_core_get_record(doc: dict, record_index: int) -> list:
    """
    Return a record (list of values) by index.
    Mirrors bejson_core_get_record.
    """
    _check_record_bounds(doc, record_index)
    return doc["Values"][record_index]


def bejson_core_get_field_values(doc: dict, field_name: str) -> list:
    """
    Return a list of all values for a named field across all records.
    Mirrors bejson_core_get_field_values.
    """
    idx = bejson_core_get_field_index(doc, field_name)
    return [record[idx] for record in doc["Values"]]


def bejson_core_query_records(doc: dict, field_name: str, search_value: Any) -> list[list]:
    """
    Return all records where field_name == search_value (exact match).
    Mirrors bejson_core_query_records.
    """
    idx = bejson_core_get_field_index(doc, field_name)
    return [record for record in doc["Values"] if record[idx] == search_value]


def bejson_core_query_records_advanced(doc: dict, **conditions) -> list[list]:
    """
    Return records matching all keyword conditions (AND logic).
    Example: bejson_core_query_records_advanced(doc, age=30, city="NYC")
    Mirrors bejson_core_query_records_advanced.
    """
    field_indices = {name: bejson_core_get_field_index(doc, name) for name in conditions}
    return [
        record
        for record in doc["Values"]
        if all(record[field_indices[name]] == val for name, val in conditions.items())
    ]


# ---------------------------------------------------------------------------
# 104DB SPECIFIC OPERATIONS
# ---------------------------------------------------------------------------

def bejson_core_get_records_by_type(doc: dict, record_type: str) -> list[list]:
    """
    Return records whose Record_Type_Parent matches record_type.
    Mirrors bejson_core_get_records_by_type.
    """
    if bejson_core_get_version(doc) != "104db":
        raise BEJSONCoreError("Operation requires 104db document", E_CORE_INVALID_OPERATION)
    return [record for record in doc["Values"] if record[0] == record_type]


def bejson_core_has_record_type(doc: dict, record_type: str) -> bool:
    """
    Return True if record_type is declared in Records_Type.
    Mirrors bejson_core_has_record_type.
    """
    return record_type in doc["Records_Type"]


def bejson_core_get_field_applicability(doc: dict, field_name: str) -> str:
    """
    Return the Record_Type_Parent for a field.
    In 104db, this must be a valid record type (no 'common').
    """
    field_def = bejson_core_get_field_def(doc, field_name)
    rtp = field_def.get("Record_Type_Parent")
    
    version = bejson_core_get_version(doc)
    if version == "104db":
        if rtp is None:
            # Check for legacy 'applies_to' only to provide a helpful error
            if "applies_to" in field_def:
                 raise BEJSONCoreError(f"Field '{field_name}' uses legacy 'applies_to'. 104db requires 'Record_Type_Parent'.", E_CORE_INVALID_OPERATION)
            raise BEJSONCoreError(f"Field '{field_name}' missing Record_Type_Parent in 104db", E_CORE_INVALID_OPERATION)
    
    return rtp or "common"


# ---------------------------------------------------------------------------
# DATA MODIFICATION
# All mutation functions return a new document dict (immutable style).
# ---------------------------------------------------------------------------

def _coerce_value(value: Any, field_type: str) -> Any:
    """Coerce and validate a raw Python value to the declared field type."""
    if field_type == "string":
        return str(value) if value is not None else ""
    if field_type in ("integer", "number"):
        try:
            return int(value) if field_type == "integer" else float(value)
        except (TypeError, ValueError):
            raise BEJSONCoreError(
                f"Cannot convert '{value}' to {field_type}", E_CORE_TYPE_CONVERSION_FAILED
            )
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise BEJSONCoreError(
            f"Cannot convert '{value}' to boolean", E_CORE_TYPE_CONVERSION_FAILED
        )
    return value


def bejson_core_set_value_at(
    doc: dict, record_index: int, field_index: int, new_value: Any
) -> dict:
    """
    Return a new document with the value at [record_index][field_index] replaced.
    Mirrors bejson_core_set_value_at.
    """
    _check_record_bounds(doc, record_index)
    _check_field_bounds(doc, field_index)

    field_def = doc["Fields"][field_index]
    coerced = _coerce_value(new_value, field_def["type"])

    doc = copy.deepcopy(doc)
    doc["Values"][record_index][field_index] = coerced
    return doc


def bejson_core_add_record(doc: dict, values: list) -> dict:
    """
    Return a new document with a record appended.
    Mirrors bejson_core_add_record.
    """
    field_count = bejson_core_get_field_count(doc)
    if len(values) != field_count:
        raise BEJSONCoreError(
            f"Record must have {field_count} values, got {len(values)}",
            E_CORE_INVALID_OPERATION,
        )
    coerced = [_coerce_value(v, doc["Fields"][i]["type"]) for i, v in enumerate(values)]
    doc = copy.deepcopy(doc)
    doc["Values"].append(coerced)
    return doc


def bejson_core_remove_record(doc: dict, record_index: int) -> dict:
    """
    Return a new document with the record at record_index removed.
    Mirrors bejson_core_remove_record.
    """
    _check_record_bounds(doc, record_index)
    doc = copy.deepcopy(doc)
    del doc["Values"][record_index]
    return doc


def bejson_core_update_field(
    doc: dict, record_index: int, field_name: str, new_value: Any
) -> dict:
    """
    Return a new document with a named field updated in a specific record.
    Mirrors bejson_core_update_field.
    """
    field_index = bejson_core_get_field_index(doc, field_name)
    return bejson_core_set_value_at(doc, record_index, field_index, new_value)


# ---------------------------------------------------------------------------
# TABLE OPERATIONS (COLUMN / ROW MANIPULATION)
# ---------------------------------------------------------------------------

def bejson_core_add_column(
    doc: dict,
    field_name: str,
    field_type: str,
    default_value: Any = None,
    record_type_parent: str = "",
) -> dict:
    """
    Return a new document with a new column appended.
    Mirrors bejson_core_add_column.
    """
    try:
        bejson_core_get_field_index(doc, field_name)
        raise BEJSONCoreError(f"Field '{field_name}' already exists", E_CORE_INVALID_OPERATION)
    except BEJSONCoreError as exc:
        if exc.code != E_CORE_FIELD_NOT_FOUND:
            raise

    new_field: dict = {"name": field_name, "type": field_type}
    if record_type_parent:
        new_field["Record_Type_Parent"] = record_type_parent

    doc = copy.deepcopy(doc)
    doc["Fields"].append(new_field)
    for record in doc["Values"]:
        record.append(default_value)
    return doc


def bejson_core_remove_column(doc: dict, field_name: str) -> dict:
    """
    Return a new document with the named column removed.
    Mirrors bejson_core_remove_column.
    """
    idx = bejson_core_get_field_index(doc, field_name)
    doc = copy.deepcopy(doc)
    del doc["Fields"][idx]
    for record in doc["Values"]:
        del record[idx]
    return doc


def bejson_core_rename_column(doc: dict, old_name: str, new_name: str) -> dict:
    """
    Return a new document with a column renamed.
    Mirrors bejson_core_rename_column.
    """
    idx = bejson_core_get_field_index(doc, old_name)
    try:
        bejson_core_get_field_index(doc, new_name)
        raise BEJSONCoreError(f"Field '{new_name}' already exists", E_CORE_INVALID_OPERATION)
    except BEJSONCoreError as exc:
        if exc.code != E_CORE_FIELD_NOT_FOUND:
            raise

    doc = copy.deepcopy(doc)
    doc["Fields"][idx]["name"] = new_name
    return doc


def bejson_core_get_column(doc: dict, field_name: str) -> list:
    """
    Return all values for a column. Mirrors bejson_core_get_column.
    Delegates to bejson_core_get_field_values.
    """
    return bejson_core_get_field_values(doc, field_name)


def bejson_core_set_column(doc: dict, field_name: str, values: list) -> dict:
    """
    Return a new document with an entire column replaced.
    Mirrors bejson_core_set_column.
    """
    idx = bejson_core_get_field_index(doc, field_name)
    record_count = bejson_core_get_record_count(doc)
    if len(values) != record_count:
        raise BEJSONCoreError(
            f"Value count ({len(values)}) must match record count ({record_count})",
            E_CORE_INVALID_OPERATION,
        )
    doc = copy.deepcopy(doc)
    for i, val in enumerate(values):
        doc["Values"][i][idx] = val
    return doc


def bejson_core_filter_rows(doc: dict, predicate) -> dict:
    """
    Return a new document containing only records for which predicate(record) is True.
    predicate receives a raw list (the record values).
    Mirrors bejson_core_filter_rows.
    """
    doc = copy.deepcopy(doc)
    doc["Values"] = [record for record in doc["Values"] if predicate(record)]
    return doc


def bejson_core_sort_by_field(doc: dict, field_name: str, ascending: bool = True) -> dict:
    """
    Return a new document with Values sorted by a named field.
    Mirrors bejson_core_sort_by_field.
    """
    idx = bejson_core_get_field_index(doc, field_name)
    doc = copy.deepcopy(doc)
    doc["Values"].sort(key=lambda r: (r[idx] is None, r[idx]), reverse=not ascending)
    return doc


# ---------------------------------------------------------------------------
# UTILITY FUNCTIONS
# ---------------------------------------------------------------------------

def bejson_core_pretty_print(doc: dict) -> str:
    """Return a pretty-printed JSON string. Mirrors bejson_core_pretty_print."""
    return json.dumps(doc, indent=2)


def bejson_core_compact_print(doc: dict) -> str:
    """Return a compact JSON string. Mirrors bejson_core_compact_print."""
    return json.dumps(doc, separators=(",", ":"))


def bejson_core_is_valid(doc: dict) -> bool:
    """
    Return True if doc is a valid BEJSON document, False otherwise.
    Mirrors bejson_core_is_valid.
    """
    try:
        bejson_validator_validate_string(json.dumps(doc))
        return True
    except (BEJSONValidationError, Exception):
        return False


def bejson_core_get_stats(doc: dict) -> dict:
    """
    Return a statistics dict for the document.
    Mirrors bejson_core_get_stats.
    """
    return {
        "version": bejson_core_get_version(doc),
        "field_count": bejson_core_get_field_count(doc),
        "record_count": bejson_core_get_record_count(doc),
        "records_types": bejson_core_get_records_types(doc),
    }

# ---------------------------------------------------------------------------
# LOCKING
# ---------------------------------------------------------------------------

def bejson_core_acquire_lock(file_path: str, timeout: int = 10) -> bool:
    """Acquire a lock file for the given file_path."""
    lock_path = file_path + ".lock"
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Use O_EXCL to ensure atomic creation
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            time.sleep(0.1)
    return False

def bejson_core_release_lock(file_path: str) -> None:
    """Release the lock file for the given file_path."""
    lock_path = file_path + ".lock"
    if os.path.exists(lock_path):
        os.unlink(lock_path)
