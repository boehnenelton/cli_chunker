/**
 * Library:     mfdb_validators.ts
 * Jurisdiction: ["TYPESCRIPT", "CORE_COMMAND"]
 * Status:      OFFICIAL — Core-Command/Lib (v1.1)
 * Author:      Elton Boehnen
 * Version:     1.1 (OFFICIAL)
 * Date:        2026-04-23
 * Description: (obj["description"] as string | null) ?? null,
      record_count: (obj["record_count"] as number | null) ?? null,
      schema_version: (obj["schema_version"] as string | null) ?? null,
      primary_key: (obj["primary_key"] as string | null) ?? null,
    } as MFDBManifestRecord;
  });
}

/**
Extract database-level metadata from manifest custom headers.
 */
import {
  BEJSONDocument,
  BEJSONValue,
  MFDBManifestRecord,
  MFDBDatabaseMeta,
  MFDBFileRole,
  ValidationResult,
  MFDBValidationError,
  MFDB_VALIDATION_CODES as E,
  MFDB_CORE_CODES,
} from "./bejson_types";
import { validateDocument as validateBEJSON } from "./bejson_validators";
import { _emitError, _emitWarning, _makeResult } from "./bejson_validators";

// ---------------------------------------------------------------------------
// Discovery algorithm
// ---------------------------------------------------------------------------


export function discoverRole(doc: unknown, filename: string): MFDBFileRole {
  if (doc === null || doc === undefined || typeof doc !== "object" || Array.isArray(doc)) {
    return "standalone";
  }
  const d = doc as BEJSONDocument;
  if (d.Format_Version === "104a" && filename.endsWith(".mfdb.bejson")) {
    return "manifest";
  }
  if (d.Format_Version === "104" && "Parent_Hierarchy" in d) {
    return "entity";
  }
  return "standalone";
}

// ---------------------------------------------------------------------------
// Level 1 — Manifest validation
// ---------------------------------------------------------------------------


export function validateManifest(
  doc: unknown,
  options: {
    
    resolvedPaths?: Set<string>;
  } = {}
): ValidationResult {
  const r = _makeResult();

  // Must be valid BEJSON 104a
  const bejsonResult = validateBEJSON(doc);
  if (!bejsonResult.valid) {
    for (const e of bejsonResult.errors) {
      _emitError(r, e.code, "[BEJSON] " + e.message, e.field, e.recordIndex);
    }
    return r;
  }

  const manifest = doc as BEJSONDocument;

  if (manifest.Format_Version !== "104a") {
    _emitError(r, E.NOT_A_MANIFEST, "Manifest must be Format_Version \"104a\", got \"" + manifest.Format_Version + "\".");
    return r;
  }

  // Records_Type must be exactly ["mfdb"]
  if (
    !Array.isArray(manifest.Records_Type) ||
    manifest.Records_Type.length !== 1 ||
    manifest.Records_Type[0] !== "mfdb"
  ) {
    _emitError(r, E.MANIFEST_RECORDS_TYPE_INVALID, "Manifest Records_Type must be exactly [\"mfdb\"].", "Records_Type");
  }

  // Required custom headers
  if (typeof manifest["MFDB_Version"] !== "string" || (manifest["MFDB_Version"] as string).trim() === "") {
    _emitError(r, MFDB_CORE_CODES.INVALID_MFDB_VERSION as unknown as number,
      "Manifest is missing required header MFDB_Version.", "MFDB_Version");
  }
  if (typeof manifest["DB_Name"] !== "string" || (manifest["DB_Name"] as string).trim() === "") {
    _emitError(r, MFDB_CORE_CODES.MISSING_DB_NAME as unknown as number,
      "Manifest is missing required header DB_Name.", "DB_Name");
  }

  // entity_name and file_path field presence
  const fieldNames = manifest.Fields.map((f) => f.name);
  if (!fieldNames.includes("entity_name")) {
    _emitError(r, E.MISSING_REQUIRED_MANIFEST_FIELD, "Manifest Fields must include \"entity_name\".", "entity_name");
  }
  if (!fieldNames.includes("file_path")) {
    _emitError(r, E.MISSING_REQUIRED_MANIFEST_FIELD, "Manifest Fields must include \"file_path\".", "file_path");
  }

  if (!r.valid) return r; // can't proceed without required fields

  const enIdx = fieldNames.indexOf("entity_name");
  const fpIdx = fieldNames.indexOf("file_path");

  const seenNames = new Set<string>();
  const seenPaths = new Set<string>();

  for (let i = 0; i < manifest.Values.length; i++) {
    const row = manifest.Values[i];

    const entityName = row[enIdx];
    const filePath = row[fpIdx];

    // Null checks
    if (entityName === null) {
      _emitError(r, E.NULL_IN_REQUIRED_MANIFEST_FIELD,
        "Values[" + i + "].entity_name must not be null.", "entity_name", i);
    }
    if (filePath === null) {
      _emitError(r, E.NULL_IN_REQUIRED_MANIFEST_FIELD,
        "Values[" + i + "].file_path must not be null.", "file_path", i);
    }

    // Uniqueness
    if (typeof entityName === "string") {
      if (seenNames.has(entityName)) {
        _emitError(r, E.DUPLICATE_ENTRY,
          "Duplicate entity_name: \"" + entityName + "\".", "entity_name", i);
      } else {
        seenNames.add(entityName);
      }
    }
    if (typeof filePath === "string") {
      if (seenPaths.has(filePath)) {
        _emitError(r, E.DUPLICATE_ENTRY,
          "Duplicate file_path: \"" + filePath + "\".", "file_path", i);
      } else {
        seenPaths.add(filePath);
      }

      // File existence (optional — caller must provide resolvedPaths)
      if (options.resolvedPaths && !options.resolvedPaths.has(filePath)) {
        _emitError(r, E.ENTITY_FILE_NOT_FOUND,
          "file_path \"" + filePath + "\" does not exist on disk.", "file_path", i);
      }
    }
  }

  return r;
}

// ---------------------------------------------------------------------------
// Level 2 — Entity file validation
// ---------------------------------------------------------------------------

export interface EntityValidationOptions {
  
  expectedEntityName: string;
  
  expectedParentHierarchy?: string;
  
  manifestRelativePath?: string;
  
  entityRelativePath?: string;
}


export function validateEntityFile(
  doc: unknown,
  options: EntityValidationOptions
): ValidationResult {
  const r = _makeResult();

  // Must be valid BEJSON 104
  const bejsonResult = validateBEJSON(doc);
  if (!bejsonResult.valid) {
    for (const e of bejsonResult.errors) {
      _emitError(r, e.code, "[BEJSON] " + e.message, e.field, e.recordIndex);
    }
    return r;
  }

  const entity = doc as BEJSONDocument;

  if (entity.Format_Version !== "104") {
    _emitError(r, E.NOT_AN_ENTITY, "Entity file must be Format_Version \"104\", got \"" + entity.Format_Version + "\".");
    return r;
  }

  // Parent_Hierarchy required
  if (!("Parent_Hierarchy" in entity) || entity.Parent_Hierarchy === undefined || entity.Parent_Hierarchy === null) {
    _emitError(r, E.MISSING_PARENT_HIERARCHY, "Entity file must contain Parent_Hierarchy key.", "Parent_Hierarchy");
  } else if (typeof entity.Parent_Hierarchy !== "string" || entity.Parent_Hierarchy.trim() === "") {
    _emitError(r, E.MISSING_PARENT_HIERARCHY, "Parent_Hierarchy must be a non-empty string.", "Parent_Hierarchy");
  }

  // Records_Type must be exactly one string
  if (!Array.isArray(entity.Records_Type) || entity.Records_Type.length !== 1) {
    _emitError(r, E.NOT_AN_ENTITY, "Entity file Records_Type must contain exactly one entry.", "Records_Type");
    return r;
  }

  // Records_Type[0] must match the registered entity_name (case-sensitive)
  const actualName = entity.Records_Type[0];
  if (actualName !== options.expectedEntityName) {
    _emitError(r, E.ENTITY_NAME_MISMATCH,
      "Entity file Records_Type[0] is \"" + actualName + "\" but manifest expects \"" + options.expectedEntityName + "\".",
      "Records_Type");
  }

  // Parent_Hierarchy path check (if caller provided expected value)
  if (
    options.expectedParentHierarchy !== undefined &&
    typeof entity.Parent_Hierarchy === "string" &&
    entity.Parent_Hierarchy !== options.expectedParentHierarchy
  ) {
    _emitError(r, E.MANIFEST_FILE_NOT_FOUND,
      "Parent_Hierarchy \"" + entity.Parent_Hierarchy + "\" does not match expected \"" + options.expectedParentHierarchy + "\".",
      "Parent_Hierarchy");
  }

  // Bidirectional check: entity's declared path must equal what the manifest recorded
  if (options.entityRelativePath !== undefined && options.manifestRelativePath !== undefined) {
    // The manifest says this entity lives at entityRelativePath.
    // The entity's Parent_Hierarchy + its own path should resolve back to manifestRelativePath.
    // We do a lightweight string-based check here — full path resolution is the caller's job.
    // We emit a warning rather than an error because resolution is environment-dependent.
    if (typeof entity.Parent_Hierarchy === "string") {
      _emitWarning(r, E.BIDIRECTIONAL_PATH_FAILED,
        "Bidirectional path check: verify that \"" + options.entityRelativePath +
        "\" + Parent_Hierarchy \"" + entity.Parent_Hierarchy +
        "\" resolves to manifest at \"" + options.manifestRelativePath + "\".",
        "Parent_Hierarchy");
    }
  }

  // No path escaping
  if (typeof entity.Parent_Hierarchy === "string") {
    if (entity.Parent_Hierarchy.includes("..") && _escapesRoot(entity.Parent_Hierarchy)) {
      _emitError(r, E.MISSING_PARENT_HIERARCHY,
        "Parent_Hierarchy must not escape the database root directory.", "Parent_Hierarchy");
    }
  }

  return r;
}

// ---------------------------------------------------------------------------
// Level 3 — Database-wide validation
// ---------------------------------------------------------------------------

export interface DatabaseValidationOptions {
  
  strict?: boolean;
  
  resolvedPaths?: Set<string>;
}


export function validateDatabase(
  manifest: unknown,
  entityDocs: Map<string, unknown>,
  options: DatabaseValidationOptions = {}
): ValidationResult {
  const r = _makeResult();

  // Level 1
  const l1 = validateManifest(manifest, { resolvedPaths: options.resolvedPaths });
  for (const e of l1.errors) _emitError(r, e.code, "[L1] " + e.message, e.field, e.recordIndex);
  for (const w of l1.warnings) _emitWarning(r, w.code, "[L1] " + w.message, w.field, w.recordIndex);
  if (!r.valid) return r;

  const manifestDoc = manifest as BEJSONDocument;
  const records = decodeManifestRecords(manifestDoc);

  // Level 2 — per entity
  for (const record of records) {
    const entityDoc = entityDocs.get(record.file_path);
    if (!entityDoc) {
      _emitError(r, E.ENTITY_FILE_NOT_FOUND,
        "[L2] Entity document not provided for file_path \"" + record.file_path + "\".", "file_path");
      continue;
    }

    const l2 = validateEntityFile(entityDoc, {
      expectedEntityName: record.entity_name,
      entityRelativePath: record.file_path,
    });
    for (const e of l2.errors) _emitError(r, e.code, "[L2:" + record.entity_name + "] " + e.message, e.field, e.recordIndex);
    for (const w of l2.warnings) _emitWarning(r, w.code, "[L2:" + record.entity_name + "] " + w.message, w.field, w.recordIndex);
  }

  // Level 3 — record_count advisory check + FK resolution (warnings only unless strict)
  if (r.valid) {
    _checkRecordCounts(manifestDoc, records, entityDocs, r);
    _checkFKResolution(records, entityDocs, options.strict === true, r);
  }

  return r;
}

// ---------------------------------------------------------------------------
// Helper — decode manifest Values into MFDBManifestRecord objects
// ---------------------------------------------------------------------------


export function decodeManifestRecords(manifest: BEJSONDocument): MFDBManifestRecord[] {
  const fieldNames = manifest.Fields.map((f) => f.name);
  return manifest.Values.map((row) => {
    const obj: Record<string, BEJSONValue> = {};
    for (let i = 0; i < fieldNames.length; i++) {
      obj[fieldNames[i]] = row[i];
    }
    return {
      entity_name: obj["entity_name"] as string,
      file_path: obj["file_path"] as string,
      description: (obj["description"] as string | null) ?? null,
      record_count: (obj["record_count"] as number | null) ?? null,
      schema_version: (obj["schema_version"] as string | null) ?? null,
      primary_key: (obj["primary_key"] as string | null) ?? null,
    } as MFDBManifestRecord;
  });
}


export function decodeDatabaseMeta(manifest: BEJSONDocument): MFDBDatabaseMeta {
  return {
    mfdb_version: (manifest["MFDB_Version"] as string) ?? "",
    db_name: (manifest["DB_Name"] as string) ?? "",
    db_description: (manifest["DB_Description"] as string) ?? undefined,
    schema_version: (manifest["Schema_Version"] as string) ?? undefined,
    author: (manifest["Author"] as string) ?? undefined,
    created_at: (manifest["Created_At"] as string) ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Level 3 sub-checks
// ---------------------------------------------------------------------------

function _checkRecordCounts(
  manifestDoc: BEJSONDocument,
  records: MFDBManifestRecord[],
  entityDocs: Map<string, unknown>,
  r: ValidationResult
): void {
  const rcIdx = manifestDoc.Fields.findIndex((f) => f.name === "record_count");
  if (rcIdx === -1) return; // field not declared — skip

  for (let i = 0; i < records.length; i++) {
    const record = records[i];
    if (record.record_count === null) continue;

    const entityDoc = entityDocs.get(record.file_path) as BEJSONDocument | undefined;
    if (!entityDoc) continue;

    const actualCount = entityDoc.Values.length;
    if (actualCount !== record.record_count) {
      _emitWarning(r, 0,
        "[L3] record_count mismatch for \"" + record.entity_name + "\": manifest says " +
        record.record_count + ", file has " + actualCount + " rows.",
        "record_count", i);
    }
  }
}

function _checkFKResolution(
  records: MFDBManifestRecord[],
  entityDocs: Map<string, unknown>,
  strict: boolean,
  r: ValidationResult
): void {
  // Build a map of primary_key field names to entity names
  const pkMap = new Map<string, string>(); // pk_field → entity_name
  for (const rec of records) {
    if (rec.primary_key) {
      pkMap.set(rec.primary_key, rec.entity_name);
    }
  }

  // For each entity, find FK fields (ending in _fk) and try to resolve them
  for (const rec of records) {
    const entityDoc = entityDocs.get(rec.file_path) as BEJSONDocument | undefined;
    if (!entityDoc) continue;

    for (const field of entityDoc.Fields) {
      if (!field.name.endsWith("_fk")) continue;

      // Derive expected PK field name: strip _fk suffix
      const expectedPK = field.name.slice(0, -3); // e.g. user_id_fk → user_id
      if (!pkMap.has(expectedPK)) {
        const msg = "[L3] FK field \"" + field.name + "\" in entity \"" + rec.entity_name +
          "\" cannot resolve to any manifest primary_key \"" + expectedPK + "\".";
        if (strict) {
          _emitError(r, E.FK_UNRESOLVED, msg, field.name);
        } else {
          _emitWarning(r, E.FK_UNRESOLVED, msg, field.name);
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------


function _escapesRoot(relPath: string): boolean {
  const parts = relPath.replace(/\\/g, "/").split("/");
  let depth = 0;
  for (const part of parts) {
    if (part === "..") {
      depth--;
      if (depth < 0) return true;
    } else if (part !== "." && part !== "") {
      depth++;
    }
  }
  return false;
}