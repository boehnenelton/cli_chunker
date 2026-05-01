"""
Library:     lib_bejson_parse.py
Jurisdiction: ["PYTHON", "CORE_COMMAND"]
Status:      OFFICIAL — Core-Command/Lib (v1.1)
Author:      Elton Boehnen
Version:     1.1 (OFFICIAL) CoreEvo fork
Date:        2026-04-23
Description: BEJSON structured parser — extracts files from BEJSON 104 / 104a / 104db schemas.
             Sources lib_bejson_core.py and lib_bejson_validator.py.
"""
import datetime
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

# ------------------------------------------------------------------
# BEJSON ecosystem — core + validator sourced here
# ------------------------------------------------------------------
from lib_bejson_core import (
    BEJSONCoreError,
    bejson_core_is_valid,
    bejson_core_get_version,
    bejson_core_get_stats,
)
from lib_bejson_validator import (
    BEJSONValidationError,
    bejson_validator_validate_string,
    bejson_validator_get_report,
)

# Default output dir (callers may override via cfg)
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT  = os.path.join(_SCRIPT_DIR, "output")

# ------------------------------------------------------------------
# ATOMIC WRITE HELPER (Generic)
# ------------------------------------------------------------------

def _atomic_write_text(file_path: str, content: str) -> None:
    """Write text to file_path atomically with fsync."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    temp_dir = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=temp_dir, suffix=".tmp", prefix=".parse_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.rename(tmp_path, file_path)
        # fsync the directory
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

# ------------------------------------------------------------------
# PARSER CORE
# ------------------------------------------------------------------

def parse_json(text):
    match = re.search(r'(\{.*\})', text, re.DOTALL)
    clean = match.group(1) if match else text
    return json.loads(clean)


def extract_data(data):
    fields = data.get("Fields", [])
    values = data.get("Values", [])
    if not values:
        return "My_Project", []

    f_map = {}
    for i, f in enumerate(fields):
        key = re.sub(r'[^a-z0-9]', '', f["name"].lower())
        f_map[key] = i

    def get_val(row, key):
        idx = f_map.get(key)
        if idx is not None and idx < len(row):
            v = row[idx]
            if v is not None:
                return str(v).strip()
        return None

    project_name = "My_Project"
    for row in values:
        for key in ("projectname", "zipfilename", "containername"):
            v = get_val(row, key)
            if v:
                project_name = v
                break
        if project_name != "My_Project":
            break

    project_name = re.sub(r'[<>:"/\\|?*]', '_', project_name)

    files = []
    for row in values:
        for i in range(1, 51):
            fname = get_val(row, "file" + str(i) + "name")
            fcont = get_val(row, "file" + str(i) + "content")
            if fname and fcont:
                files.append({"name": fname, "content": fcont})

    return project_name, files


def save_files(proj, files, cfg):
    base_dir = cfg.get("output_path") or DEFAULT_OUT
    if not os.path.isdir(base_dir):
        try:
            os.makedirs(base_dir, exist_ok=True)
        except Exception as e:
            return {"success": False, "message": "Cannot create output dir: " + str(e)}

    overwrite = cfg.get("overwrite_enabled", False)

    if overwrite:
        target     = os.path.join(base_dir, proj)
        bak_target = os.path.join(base_dir, proj + "_BACKUP")
        if os.path.exists(target):
            if os.path.exists(bak_target):
                shutil.rmtree(bak_target, ignore_errors=True)
            try:
                shutil.copytree(target, bak_target)
            except Exception as e:
                print("Backup warning: " + str(e))
    else:
        ts     = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(base_dir, ts + "_" + proj)

    try:
        os.makedirs(target, exist_ok=True)

        for f in files:
            fpath = os.path.join(target, f["name"])
            _atomic_write_text(fpath, f["content"])

        # Build report
        ts_now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode_str = "Merge/Update (overwrite)" if overwrite else "Timestamped (new folder)"
        lines    = []
        lines.append("=" * 52)
        lines.append("  STRUCTURED PARSER — BUILD REPORT")
        lines.append("=" * 52)
        lines.append("Project    : " + proj)
        lines.append("Generated  : " + ts_now)
        lines.append("Mode       : " + mode_str)
        lines.append("Output Dir : " + target)
        lines.append("Files      : " + str(len(files)))
        lines.append("-" * 52)
        lines.append("FILE LIST")
        lines.append("-" * 52)
        for idx, f in enumerate(files):
            size_b = len(f["content"].encode("utf-8"))
            if size_b >= 1024:
                size_s = str(round(size_b / 1024.0, 1)) + " KB"
            else:
                size_s = str(size_b) + " B"
            lines.append("  [" + str(idx + 1).zfill(2) + "] " + f["name"] + "  (" + size_s + ")")
        lines.append("-" * 52)
        lines.append("Zip        : " + proj + "_update.zip")
        lines.append("=" * 52)
        report_text = "\n".join(lines) + "\n"

        # Write report to disk atomically
        report_path = os.path.join(target, "_REPORT.txt")
        _atomic_write_text(report_path, report_text)

        # Build zip (files + report)
        zip_path = os.path.join(target, proj + "_update.zip")
        # Zip is somewhat atomic if we write to temp and rename, but zipfile doesn't support fsync directly easily.
        # For this library, we'll focus on the text files being atomic.
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.writestr(f["name"], f["content"])
            zf.writestr("_REPORT.txt", report_text)

        return {
            "success":    True,
            "message":    "Saved " + str(len(files)) + " file(s)",
            "path":       target,
            "file_count": len(files),
        }

    except Exception as e:
        return {"success": False, "message": str(e)}
