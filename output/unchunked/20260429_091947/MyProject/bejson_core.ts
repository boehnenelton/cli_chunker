/**
 * Library:     bejson_core.ts
 * Jurisdiction: ["TYPESCRIPT", "CORE_COMMAND"]
 * Status:      OFFICIAL — Core-Command/Lib (v1.1)
 * Author:      Elton Boehnen
 * Version:     1.1 (OFFICIAL)
 * Date:        2026-04-23
 * Description: Core-Command library component.
 */
import {
  BEJSONDocument,
  BEJSONField,
  BEJSONValue,
  BEJSONCoreError,
  BEJSON_CORE_CODES,
} from "./bejson_types";

// ---------------------------------------------------------------------------
// Parse & Serialize
// ---------------------------------------------------------------------------


export function parse(json: string): BEJSONDocument {
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch (e) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.PARSE_ERROR,
      "Invalid JSON: " + String(e)
    );
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.PARSE_ERROR,
      "Parsed JSON root must be an object."
    );
  }
  return raw as BEJSONDocument;
}


export function serialize(doc: BEJSONDocument, indent: number = 2): string {
  if (doc === null || doc === undefined) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.NULL_DOCUMENT,
      "Cannot serialize null or undefined document."
    );
  }
  try {
    return JSON.stringify(doc, null, indent || undefined);
  } catch (e) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.SERIALIZATION_ERROR,
      "Serialization failed: " + String(e)
    );
  }
}

// ---------------------------------------------------------------------------
// Field index helpers
// ---------------------------------------------------------------------------


export function getFieldIndex(doc: BEJSONDocument, name: string): number {
  _assertDoc(doc);
  const idx = doc.Fields.findIndex((f) => f.name === name);
  if (idx === -1) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.FIELD_NOT_FOUND,
      "Field not found: " + name
    );
  }
  return idx;
}


export function getFieldNames(doc: BEJSONDocument): string[] {
  _assertDoc(doc);
  return doc.Fields.map((f) => f.name);
}


export function getFields(doc: BEJSONDocument): BEJSONField[] {
  _assertDoc(doc);
  return doc.Fields.map((f) => Object.assign({}, f));
}

// ---------------------------------------------------------------------------
// Record accessors
// ---------------------------------------------------------------------------


export function getRecord(
  doc: BEJSONDocument,
  index: number
): Record<string, BEJSONValue> {
  _assertDoc(doc);
  _assertIndex(doc, index);
  return _rowToObject(doc.Fields, doc.Values[index]);
}


export function getAllRecords(
  doc: BEJSONDocument
): Record<string, BEJSONValue>[] {
  _assertDoc(doc);
  return doc.Values.map((row) => _rowToObject(doc.Fields, row));
}


export function getFieldValue(
  doc: BEJSONDocument,
  index: number,
  fieldName: string
): BEJSONValue {
  _assertDoc(doc);
  _assertIndex(doc, index);
  const fi = getFieldIndex(doc, fieldName);
  return doc.Values[index][fi];
}


export function getRecordCount(doc: BEJSONDocument): number {
  _assertDoc(doc);
  return doc.Values.length;
}

// ---------------------------------------------------------------------------
// 104db — entity-scoped record access
// ---------------------------------------------------------------------------


export function getRecordsByType(
  doc: BEJSONDocument,
  type: string
): Record<string, BEJSONValue>[] {
  _assertDoc(doc);
  if (doc.Format_Version !== "104db") {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.UNSUPPORTED_OPERATION,
      "getRecordsByType is only valid on BEJSON 104db documents."
    );
  }
  // In 104db, the first field MUST be Record_Type_Parent (index 0)
  return doc.Values.filter((row) => row[0] === type).map((row) =>
    _rowToObject(doc.Fields, row)
  );
}


export function getFieldApplicability(
  doc: BEJSONDocument,
  fieldName: string
): string {
  _assertDoc(doc);
  const field = doc.Fields.find((f) => f.name === fieldName);
  if (!field) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.FIELD_NOT_FOUND,
      `Field not found: ${fieldName}`
    );
  }

  const rtp = field.Record_Type_Parent;
  if (doc.Format_Version === "104db") {
    if (!rtp) {
      if ((field as any).applies_to) {
        throw new BEJSONCoreError(
          BEJSON_CORE_CODES.UNSUPPORTED_OPERATION,
          `Field '${fieldName}' uses legacy 'applies_to'. 104db requires 'Record_Type_Parent'.`
        );
      }
      throw new BEJSONCoreError(
        BEJSON_CORE_CODES.UNSUPPORTED_OPERATION,
        `Field '${fieldName}' missing Record_Type_Parent in 104db`
      );
    }
  }
  return rtp || "common";
}


export function queryRecords(
  doc: BEJSONDocument,
  fieldName: string,
  searchValue: BEJSONValue
): Record<string, BEJSONValue>[] {
  _assertDoc(doc);
  const idx = getFieldIndex(doc, fieldName);
  return doc.Values.filter((row) => row[idx] === searchValue).map((row) =>
    _rowToObject(doc.Fields, row)
  );
}


export function sortByField(
  doc: BEJSONDocument,
  fieldName: string,
  ascending: boolean = true
): BEJSONDocument {
  _assertDoc(doc);
  const idx = getFieldIndex(doc, fieldName);
  const sortedValues = [...doc.Values].sort((a, b) => {
    const valA = a[idx];
    const valB = b[idx];
    if (valA === valB) return 0;
    if (valA === null) return 1;
    if (valB === null) return -1;
    
    let comparison = 0;
    if (typeof valA === 'string' && typeof valB === 'string') {
      comparison = valA.localeCompare(valB);
    } else {
      comparison = (valA as any) < (valB as any) ? -1 : 1;
    }
    return ascending ? comparison : -comparison;
  });

  return _cloneWith(doc, { Values: sortedValues });
}


export function getEntityFields(
  doc: BEJSONDocument,
  entityName: string
): BEJSONField[] {
  _assertDoc(doc);
  if (doc.Format_Version !== "104db") {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.UNSUPPORTED_OPERATION,
      "getEntityFields is only valid on BEJSON 104db documents."
    );
  }
  return doc.Fields.filter(
    (f) => f.name !== "Record_Type_Parent" && f.Record_Type_Parent === entityName
  );
}

// ---------------------------------------------------------------------------
// Record mutations (return new document — documents are treated as immutable)
// ---------------------------------------------------------------------------


export function appendRecord(
  doc: BEJSONDocument,
  values: BEJSONValue[]
): BEJSONDocument {
  _assertDoc(doc);
  _assertRowLength(doc, values);
  const coerced = values.map((v, i) => _coerceValue(v, doc.Fields[i].type));
  return _cloneWith(doc, { Values: [...doc.Values, coerced] });
}


export function updateRecord(
  doc: BEJSONDocument,
  index: number,
  values: BEJSONValue[]
): BEJSONDocument {
  _assertDoc(doc);
  _assertIndex(doc, index);
  _assertRowLength(doc, values);
  const coerced = values.map((v, i) => _coerceValue(v, doc.Fields[i].type));
  const newValues = doc.Values.map((row, i) =>
    i === index ? coerced : row
  );
  return _cloneWith(doc, { Values: newValues });
}


export function setFieldValue(
  doc: BEJSONDocument,
  index: number,
  fieldName: string,
  value: BEJSONValue
): BEJSONDocument {
  _assertDoc(doc);
  _assertIndex(doc, index);
  const fi = getFieldIndex(doc, fieldName);
  const coerced = _coerceValue(value, doc.Fields[fi].type);
  const newValues = doc.Values.map((row, i) => {
    if (i !== index) return row;
    const newRow = [...row];
    newRow[fi] = coerced;
    return newRow;
  });
  return _cloneWith(doc, { Values: newValues });
}


export function deleteRecord(
  doc: BEJSONDocument,
  index: number
): BEJSONDocument {
  _assertDoc(doc);
  _assertIndex(doc, index);
  const newValues = doc.Values.filter((_, i) => i !== index);
  return _cloneWith(doc, { Values: newValues });
}

// ---------------------------------------------------------------------------
// Schema mutations
// ---------------------------------------------------------------------------


export function appendField(
  doc: BEJSONDocument,
  field: BEJSONField,
  defaultValue: BEJSONValue = null
): BEJSONDocument {
  _assertDoc(doc);
  if (doc.Fields.some((f) => f.name === field.name)) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.WRITE_LENGTH_MISMATCH,
      "Field already exists: " + field.name
    );
  }
  const newFields = [...doc.Fields, { ...field }];
  const newValues = doc.Values.map((row) => [...row, defaultValue]);
  return _cloneWith(doc, { Fields: newFields, Values: newValues });
}

// ---------------------------------------------------------------------------
// Document factory helpers
// ---------------------------------------------------------------------------


export function createEmpty104(
  recordType: string,
  fields: BEJSONField[],
  parentHierarchy?: string
): BEJSONDocument {
  const doc: BEJSONDocument = {
    Format: "BEJSON",
    Format_Version: "104",
    Format_Creator: "Elton Boehnen",
    Records_Type: [recordType],
    Fields: fields.map((f) => ({ ...f })),
    Values: [],
  };
  if (parentHierarchy !== undefined) {
    doc.Parent_Hierarchy = parentHierarchy;
  }
  return doc;
}


export function createEmpty104a(
  recordType: string,
  fields: BEJSONField[],
  customHeaders: Record<string, string | number | boolean> = {}
): BEJSONDocument {
  const doc: BEJSONDocument = {
    Format: "BEJSON",
    Format_Version: "104a",
    Format_Creator: "Elton Boehnen",
    ...customHeaders,
    Records_Type: [recordType],
    Fields: fields.map((f) => ({ ...f })),
    Values: [],
  };
  return doc;
}


export function createEmpty104db(
  recordTypes: string[],
  entityFields: BEJSONField[]
): BEJSONDocument {
  const discriminator: BEJSONField = {
    name: "Record_Type_Parent",
    type: "string",
  };
  return {
    Format: "BEJSON",
    Format_Version: "104db",
    Format_Creator: "Elton Boehnen",
    Records_Type: [...recordTypes],
    Fields: [discriminator, ...entityFields.map((f) => ({ ...f }))],
    Values: [],
  };
}

// ---------------------------------------------------------------------------
// Utility — flatten a record for 104db (strip discriminator, nulled fields)
// ---------------------------------------------------------------------------


export function flattenEntityRecord(
  doc: BEJSONDocument,
  record: Record<string, BEJSONValue>
): Record<string, BEJSONValue> {
  if (doc.Format_Version !== "104db") {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.UNSUPPORTED_OPERATION,
      "flattenEntityRecord is only valid on BEJSON 104db documents."
    );
  }
  const entityName = record["Record_Type_Parent"] as string;
  const result: Record<string, BEJSONValue> = {};
  for (const field of doc.Fields) {
    if (field.name === "Record_Type_Parent") continue;
    if (field.Record_Type_Parent === entityName) {
      result[field.name] = record[field.name];
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _assertDoc(doc: BEJSONDocument): void {
  if (doc === null || doc === undefined) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.NULL_DOCUMENT,
      "Document is null or undefined."
    );
  }
}

function _assertIndex(doc: BEJSONDocument, index: number): void {
  if (index < 0 || index >= doc.Values.length) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.INDEX_OUT_OF_BOUNDS,
      "Record index " + index + " is out of bounds (length " + doc.Values.length + ")."
    );
  }
}

function _assertRowLength(doc: BEJSONDocument, values: BEJSONValue[]): void {
  if (values.length !== doc.Fields.length) {
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.WRITE_LENGTH_MISMATCH,
      "Row length " + values.length + " does not match Fields length " + doc.Fields.length + "."
    );
  }
}

function _rowToObject(
  fields: BEJSONField[],
  row: BEJSONValue[]
): Record<string, BEJSONValue> {
  const obj: Record<string, BEJSONValue> = {};
  for (let i = 0; i < fields.length; i++) {
    obj[fields[i].name] = row[i];
  }
  return obj;
}

function _cloneWith(
  doc: BEJSONDocument,
  overrides: Partial<BEJSONDocument>
): BEJSONDocument {
  return Object.assign({}, doc, overrides);
}

function _coerceValue(value: any, fieldType: string): BEJSONValue {
  if (fieldType === "string") return String(value);
  if (fieldType === "integer" || fieldType === "number") {
    const num =
      fieldType === "integer" ? parseInt(value, 10) : parseFloat(value);
    if (isNaN(num)) {
      throw new BEJSONCoreError(
        BEJSON_CORE_CODES.WRITE_TYPE_MISMATCH,
        `Cannot convert '${value}' to ${fieldType}`
      );
    }
    return num;
  }
  if (fieldType === "boolean") {
    if (typeof value === "boolean") return value;
    if (typeof value === "string") {
      if (value.toLowerCase() === "true") return true;
      if (value.toLowerCase() === "false") return false;
    }
    throw new BEJSONCoreError(
      BEJSON_CORE_CODES.WRITE_TYPE_MISMATCH,
      `Cannot convert '${value}' to boolean`
    );
  }
  return value as BEJSONValue;
}