"""
Library:     lib_bejson_validator.py
Jurisdiction: ["PYTHON", "CORE_COMMAND"]
Status:      OFFICIAL — Core-Command/Lib (v1.21)
Author:      Elton Boehnen
Version:     1.21 (OFFICIAL) CoreEvo alignment
Date:        2026-04-27
Description: BEJSON validator — schema validation for 104, 104a, 104db.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

E_INVALID_JSON = 1
E_MISSING_MANDATORY_KEY = 2
E_INVALID_FORMAT = 3
E_INVALID_VERSION = 4
E_INVALID_RECORDS_TYPE = 5
E_INVALID_FIELDS = 6
E_INVALID_VALUES = 7
E_TYPE_MISMATCH = 8
E_RECORD_LENGTH_MISMATCH = 9
E_RESERVED_KEY_COLLISION = 10
E_INVALID_RECORD_TYPE_PARENT = 11
E_NULL_VIOLATION = 12
E_FILE_NOT_FOUND = 13
E_PERMISSION_DENIED = 14

VALID_VERSIONS = {"104", "104a", "104db"}
MANDATORY_KEYS = ("Format", "Format_Version", "Format_Creator", "Records_Type", "Fields", "Values")

@dataclass
class ValidationState:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    current_file: str = ""
    def reset(self):
        self.errors.clear()
        self.warnings.clear()
        self.current_file = ""

_state = ValidationState()

class BEJSONValidationError(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code

def bejson_validator_reset_state(): _state.reset()
def bejson_validator_get_errors(): return _state.errors
def bejson_validator_error_count(): return len(_state.errors)
def bejson_validator_has_errors(): return len(_state.errors) > 0
def bejson_validator_get_warnings(): return _state.warnings
def bejson_validator_warning_count(): return len(_state.warnings)
def bejson_validator_has_warnings(): return len(_state.warnings) > 0

def bejson_validator_check_json_syntax(input_, is_file=False):
    if is_file:
        path = Path(input_)
        if not path.exists(): raise BEJSONValidationError(f"File not found: {input_}", E_FILE_NOT_FOUND)
        text = path.read_text(encoding="utf-8")
        _state.current_file = str(path)
    else: text = input_
    if isinstance(text, dict): return text
    try: return json.loads(text)
    except Exception as e: raise BEJSONValidationError(f"Invalid JSON: {e}", E_INVALID_JSON)

def bejson_validator_check_mandatory_keys(doc):
    for key in MANDATORY_KEYS:
        if key not in doc: raise BEJSONValidationError(f"Missing key: {key}", E_MISSING_MANDATORY_KEY)
    if doc["Format"] != "BEJSON": raise BEJSONValidationError("Invalid Format", E_INVALID_FORMAT)
    if doc["Format_Creator"] != "Elton Boehnen":
        raise BEJSONValidationError("Invalid Format_Creator: Must be 'Elton Boehnen'", E_INVALID_FORMAT)
    version = doc.get("Format_Version", "")
    if version not in VALID_VERSIONS: raise BEJSONValidationError(f"Invalid version: {version}", E_INVALID_VERSION)
    return version

def bejson_validator_check_records_type(doc, version):
    rt = doc["Records_Type"]
    if not isinstance(rt, list):
        raise BEJSONValidationError("Records_Type must be a list", E_INVALID_RECORDS_TYPE)
    count = len(rt)
    if version in ("104", "104a"):
        if count != 1:
            raise BEJSONValidationError(f"BEJSON {version} must have exactly 1 record type. Found {count}.", E_INVALID_RECORDS_TYPE)
    elif version == "104db":
        if count < 2:
            raise BEJSONValidationError("104db requires 2+ types", E_INVALID_RECORDS_TYPE)

def bejson_validator_check_record_type_parent(doc, version):
    """Specific check for Record_Type_Parent consistency in 104db."""
    if version != "104db":
        return True
    
    fields = doc["Fields"]
    if not fields or fields[0].get("name") != "Record_Type_Parent":
        raise BEJSONValidationError("104db first field must be 'Record_Type_Parent'", E_INVALID_RECORD_TYPE_PARENT)
    
    valid_types = set(doc["Records_Type"])
    for i, record in enumerate(doc["Values"]):
        rtp = record[0]
        if rtp not in valid_types:
            raise BEJSONValidationError(f"Invalid Record_Type_Parent '{rtp}' at row {i}", E_INVALID_RECORD_TYPE_PARENT)
    return True

def bejson_validator_check_fields_structure(doc, version):
    fields = doc["Fields"]
    for i, f in enumerate(fields):
        fname = f.get("name")
        ftype = f.get("type")
        if not fname or not ftype:
            raise BEJSONValidationError(f"Field {i} missing name or type", E_INVALID_FIELDS)
        
        if version == "104a" and ftype in ("array", "object"):
            raise BEJSONValidationError(f"104a forbids complex type: {ftype}", E_INVALID_FIELDS)
            
        if version == "104db":
            if fname != "Record_Type_Parent" and "Record_Type_Parent" not in f:
                 raise BEJSONValidationError(f"Field '{fname}' missing Record_Type_Parent in 104db", E_INVALID_RECORD_TYPE_PARENT)
    return len(fields)

def bejson_validator_check_values(doc, version, fields_count):
    fields = doc["Fields"]
    for i, record in enumerate(doc["Values"]):
        if len(record) != fields_count:
            raise BEJSONValidationError(f"Length mismatch at row {i}", E_RECORD_LENGTH_MISMATCH)
        
        # Type validation
        for j, val in enumerate(record):
            ftype = fields[j].get("type")
            if val is None:
                continue # Nulls are generally allowed unless specified otherwise
            
            if ftype == "string" and not isinstance(val, str):
                 raise BEJSONValidationError(f"Type mismatch at row {i}, col {j}: expected string", E_TYPE_MISMATCH)
            if ftype == "integer" and not isinstance(val, int):
                 raise BEJSONValidationError(f"Type mismatch at row {i}, col {j}: expected integer", E_TYPE_MISMATCH)
            if ftype == "number" and not isinstance(val, (int, float)):
                 raise BEJSONValidationError(f"Type mismatch at row {i}, col {j}: expected number", E_TYPE_MISMATCH)
            if ftype == "boolean" and not isinstance(val, bool):
                 raise BEJSONValidationError(f"Type mismatch at row {i}, col {j}: expected boolean", E_TYPE_MISMATCH)

def bejson_validator_check_dependencies(doc):
    """Stub for dependency checking - to be implemented if needed by spec."""
    return True

def bejson_validator_check_custom_headers(doc, version):
    mandatory_set = set(MANDATORY_KEYS)
    for key in doc:
        if key in mandatory_set or key == "Parent_Hierarchy": continue
        if version in ("104", "104db"):
            raise BEJSONValidationError(f"Custom key '{key}' forbidden in {version}", E_RESERVED_KEY_COLLISION)

def bejson_validator_validate_string(json_string):
    bejson_validator_reset_state()
    doc = bejson_validator_check_json_syntax(json_string)
    version = bejson_validator_check_mandatory_keys(doc)
    bejson_validator_check_custom_headers(doc, version)
    bejson_validator_check_records_type(doc, version)
    bejson_validator_check_record_type_parent(doc, version)
    fields_count = bejson_validator_check_fields_structure(doc, version)
    bejson_validator_check_values(doc, version, fields_count)
    bejson_validator_check_dependencies(doc)
    return True

def bejson_validator_validate_file(file_path):
    text = Path(file_path).read_text(encoding="utf-8")
    return bejson_validator_validate_string(text)

def bejson_validator_get_report(json_string, is_file=False):
    valid = False
    try:
        valid = bejson_validator_validate_file(json_string) if is_file else bejson_validator_validate_string(json_string)
    except: pass
    rep = [f"Status: {'VALID' if valid else 'INVALID'}", f"Errors: {len(_state.errors)}"]
    if _state.errors: rep.extend(_state.errors)
    return "\n".join(rep)
