#!/usr/bin/env python3
"""
CLI Chunker - Project to BEJSON 104db Packager & Rebuilder
Description: Standardized tool for "chunking" (packing) projects into BEJSON 
             and "unchunking" (unpacking) them back into a directory structure.
"""
import os
import sys
import json
import argparse
import time
from pathlib import Path

# Setup local library path
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "lib"))

try:
    import lib_bejson_core as BEJSONCore
except ImportError:
    print("CRITICAL: Local libraries not found in lib/")
    sys.exit(1)

DEFAULT_CONFIG = {
    "project_name": "MyProject",
    "version": "1.0.0",
    "extensions": [".py", ".js", ".ts", ".html", ".css", ".md", ".json", ".sh", ".txt", ".bejson"],
    "exclude_dirs": [".git", "__pycache__", "node_modules", "lib", "output", ".mfdb_lock"],
    "output_base": str(BASE_DIR / "output")
}

def get_timestamp():
    return time.strftime("%Y%m%d_%H%M%S")

def load_or_create_config(target_path):
    config_path = Path(target_path) / "chunker_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in config: config[k] = v
                return config
        except Exception as e:
            print(f"Warning: Failed to read config, using defaults. Error: {e}")
    
    try:
        with open(config_path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"[*] Created default config at {config_path}")
    except Exception as e:
        print(f"Warning: Could not create config file. Error: {e}")
        
    return DEFAULT_CONFIG

def is_binary(file_path):
    try:
        with open(file_path, 'tr') as check_file:
            check_file.read(512)
            return False
    except UnicodeDecodeError:
        return True

def run_chunk(target_dir):
    target_path = Path(target_dir).resolve()
    if not target_path.is_dir():
        print(f"Error: {target_dir} is not a directory.")
        return

    config = load_or_create_config(target_path)
    exts = config["extensions"]
    excludes = config["exclude_dirs"]
    
    print(f"[*] Mode: CHUNK")
    print(f"[*] Target: {target_path}")
    
    records_types = ["ProjectMeta", "FileContent"]
    fields = [
        {"name": "Record_Type_Parent", "type": "string"},
        {"name": "project_name", "type": "string", "Record_Type_Parent": "ProjectMeta"},
        {"name": "version", "type": "string", "Record_Type_Parent": "ProjectMeta"},
        {"name": "root_path", "type": "string", "Record_Type_Parent": "ProjectMeta"},
        {"name": "file_path", "type": "string", "Record_Type_Parent": "FileContent"},
        {"name": "file_name", "type": "string", "Record_Type_Parent": "FileContent"},
        {"name": "content", "type": "string", "Record_Type_Parent": "FileContent"},
        {"name": "is_binary", "type": "boolean", "Record_Type_Parent": "FileContent"}
    ]
    
    values = []
    values.append(["ProjectMeta", config["project_name"], config["version"], str(target_path), None, None, None, None])
    
    file_count = 0
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if d not in excludes]
        for file in files:
            f_path = Path(root) / file
            if f_path.suffix.lower() in exts:
                try:
                    rel_path = f_path.relative_to(target_path)
                    if file == "chunker_config.json": continue
                        
                    binary = is_binary(f_path)
                    content = "" if binary else f_path.read_text(encoding="utf-8")
                    
                    values.append(["FileContent", None, None, None, str(rel_path), file, content, binary])
                    file_count += 1
                    print(f"  [+] {rel_path}")
                except Exception as e:
                    print(f"  [-] Failed to process {file}: {e}")

    doc = BEJSONCore.bejson_core_create_104db(records_types, fields, values)
    
    out_dir = Path(config["output_base"]) / "chunked" / get_timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{config['project_name']}.104db.bejson"
    
    try:
        BEJSONCore.bejson_core_atomic_write(str(out_file), doc)
        print(f"\n[SUCCESS] Project chunked into {out_file}")
        print(f"[*] Total Files: {file_count}")
    except Exception as e:
        print(f"\n[ERROR] Failed to save BEJSON: {e}")

def run_unchunk(bejson_file):
    input_path = Path(bejson_file).resolve()
    if not input_path.exists():
        print(f"Error: File {bejson_file} not found.")
        return

    print(f"[*] Mode: UNCHUNK")
    print(f"[*] Source: {input_path}")
    
    try:
        doc = BEJSONCore.bejson_core_load_file(str(input_path))
        if BEJSONCore.bejson_core_get_version(doc) != "104db":
            print("Error: Input is not a valid BEJSON 104db file.")
            return

        # Extract Meta
        meta_rows = BEJSONCore.bejson_core_get_records_by_type(doc, "ProjectMeta")
        if not meta_rows:
            print("Error: No ProjectMeta record found.")
            return
        
        # Mapping indices based on field schema
        fields = [f["name"] for f in doc["Fields"]]
        pname_idx = fields.index("project_name")
        fpath_idx = fields.index("file_path")
        fname_idx = fields.index("file_name")
        cont_idx = fields.index("content")
        bin_idx = fields.index("is_binary")
        
        proj_name = meta_rows[0][pname_idx]
        
        # Setup Output Dir
        out_dir = Path(DEFAULT_CONFIG["output_base"]) / "unchunked" / get_timestamp() / proj_name
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Extract Files
        file_rows = BEJSONCore.bejson_core_get_records_by_type(doc, "FileContent")
        for row in file_rows:
            rel_path = row[fpath_idx]
            content = row[cont_idx]
            binary = row[bin_idx]
            
            if rel_path:
                target_file = out_dir / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                
                if binary:
                    # In this simple tool, binaries are just placeholders or empty strings
                    # unless extended to use base64.
                    target_file.touch()
                else:
                    target_file.write_text(content, encoding="utf-8")
                print(f"  [>] {rel_path}")

        print(f"\n[SUCCESS] Project rebuilt at {out_dir}")
        print(f"[*] Total Files: {len(file_rows)}")

    except Exception as e:
        print(f"\n[ERROR] Unchunking failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="BEJSON Project Chunker/Unchunker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chunk", metavar="DIR", help="Chunk a directory into BEJSON")
    group.add_argument("--unchunk", metavar="FILE", help="Unchunk a BEJSON file into a directory")
    
    args = parser.parse_args()
    
    if args.chunk:
        run_chunk(args.chunk)
    elif args.unchunk:
        run_unchunk(args.unchunk)

if __name__ == "__main__":
    main()
