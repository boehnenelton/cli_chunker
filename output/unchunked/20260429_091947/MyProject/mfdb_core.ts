/**
 * Library:     mfdb_core.ts
 * Jurisdiction: ["TYPESCRIPT", "CORE_COMMAND"]
 * Status:      OFFICIAL — Core-Command/Lib (v1.2)
 * Author:      Elton Boehnen
 * Version:     1.2 (OFFICIAL) Archive Transport Update
 * Date:        2026-04-26
 * Description: MFDB Core definitions for TypeScript.
 *              v1.2 adds MFDBArchive support for .mfdb.zip handling.
 */
import {
  BEJSONDocument,
  BEJSONField,
  BEJSONValue,
  MFDBManifestRecord,
  MFDBDatabaseMeta,
  MFDBCoreError,
  MFDB_CORE_CODES as E,
} from "./bejson_types";
import { decodeManifestRecords } from "./mfdb_validators";
import {
  appendRecord,
  deleteRecord,
  getFieldIndex,
  setFieldValue,
  createEmpty104a,
  createEmpty104,
} from "./bejson_core";

// ---------------------------------------------------------------------------
// MFDBArchive Interface (v1.2)
// ---------------------------------------------------------------------------

/**
 * Handles .mfdb.zip packaging and virtual mounting using File System Access API.
 */
export interface MFDBArchiveInterface {
  /**
   * Mounts a .mfdb.zip file into a FileSystemDirectoryHandle.
   */
  mount(zipFile: File | Blob, dirHandle: any): Promise<string>;

  /**
   * Repacks a FileSystemDirectoryHandle back into a .mfdb.zip Blob.
   */
  commit(dirHandle: any): Promise<Blob>;
}

// ---------------------------------------------------------------------------
// Manifest factory
// ---------------------------------------------------------------------------

export interface CreateManifestOptions extends MFDBDatabaseMeta {
  includeOptionalFields?: boolean;
}

export function createManifest(opts: CreateManifestOptions): BEJSONDocument {
  if (!opts.db_name || opts.db_name.trim() === "") {
    throw new MFDBCoreError(E.MISSING_DB_NAME, "DB_Name is required when creating a manifest.");
  }

  const includeOptional = opts.includeOptionalFields !== false;

  const fields: BEJSONField[] = [
    { name: "entity_name", type: "string" },
    { name: "file_path", type: "string" },
  ];
  if (includeOptional) {
    fields.push(
      { name: "description", type: "string" },
      { name: "record_count", type: "integer" },
      { name: "schema_version", type: "string" },
      { name: "primary_key", type: "string" }
    );
  }

  const customHeaders: Record<string, string> = {
    MFDB_Version: opts.mfdb_version ?? "1.21",
    DB_Name: opts.db_name,
  };
  if (opts.db_description) customHeaders["DB_Description"] = opts.db_description;
  if (opts.schema_version) customHeaders["Schema_Version"] = opts.schema_version;
  if (opts.author) customHeaders["Author"] = opts.author;
  if (opts.created_at) customHeaders["Created_At"] = opts.created_at;

  return createEmpty104a("mfdb", fields, customHeaders);
}

// ---------------------------------------------------------------------------
// Entity registration
// ---------------------------------------------------------------------------

export function registerEntity(
  manifest: BEJSONDocument,
  record: MFDBManifestRecord
): BEJSONDocument {
  _assertManifest(manifest);

  const existing = decodeManifestRecords(manifest);
  if (existing.some((r) => r.entity_name === record.entity_name)) {
    throw new MFDBCoreError(
      E.DUPLICATE_ENTITY_NAME,
      "Entity \"" + record.entity_name + "\" is already registered."
    );
  }

  const fieldNames = manifest.Fields.map((f) => f.name);
  const row: BEJSONValue[] = fieldNames.map((name) => {
    switch (name) {
      case "entity_name": return record.entity_name;
      case "file_path": return record.file_path;
      case "description": return record.description ?? null;
      case "record_count": return record.record_count ?? null;
      case "schema_version": return record.schema_version ?? null;
      case "primary_key": return record.primary_key ?? null;
      default: return null;
    }
  });

  return appendRecord(manifest, row);
}

export function unregisterEntity(
  manifest: BEJSONDocument,
  entityName: string
): BEJSONDocument {
  _assertManifest(manifest);
  const idx = _findEntityIndex(manifest, entityName);
  return deleteRecord(manifest, idx);
}

export function syncRecordCount(
  manifest: BEJSONDocument,
  entityName: string,
  count: number
): BEJSONDocument {
  _assertManifest(manifest);
  const idx = _findEntityIndex(manifest, entityName);

  try {
    getFieldIndex(manifest, "record_count");
  } catch {
    throw new MFDBCoreError(
      E.RECORD_COUNT_SYNC_FAILED,
      "Manifest lacks \"record_count\" field."
    );
  }

  return setFieldValue(manifest, idx, "record_count", count);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _assertManifest(doc: BEJSONDocument): void {
  if (!doc) {
    throw new MFDBCoreError(E.NULL_MANIFEST, "Manifest is null or undefined.");
  }
}

function _findEntityIndex(manifest: BEJSONDocument, entityName: string): number {
  const enIdx = getFieldIndex(manifest, "entity_name");
  for (let i = 0; i < manifest.Values.length; i++) {
    if (manifest.Values[i][enIdx] === entityName) return i;
  }
  throw new MFDBCoreError(
    E.ENTITY_NOT_IN_MANIFEST,
    "Entity \"" + entityName + "\" not found."
  );
}