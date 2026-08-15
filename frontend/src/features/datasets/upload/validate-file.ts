/** Client-side pre-checks so a 200 MB file is not sent only to be rejected. */

export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;
export const MAX_EXCEL_BYTES = 25 * 1024 * 1024;
export const ACCEPTED_EXTENSIONS = [".csv", ".tsv", ".txt", ".xlsx"] as const;

export type FileValidation = { ok: true } | { ok: false; code: string; message: string };

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index).toLowerCase();
}

export function validateFile(file: File): FileValidation {
  // Validate on the extension, never file.type: browsers report CSV as
  // text/csv, application/vnd.ms-excel, text/plain or "" depending on the OS
  // and which applications are installed.
  const extension = extensionOf(file.name);

  if (!ACCEPTED_EXTENSIONS.includes(extension as (typeof ACCEPTED_EXTENSIONS)[number])) {
    return {
      ok: false,
      code: "UNSUPPORTED_FILE_TYPE",
      message: `"${extension || file.name}" is not supported. Upload a CSV, TSV or XLSX file.`,
    };
  }

  if (file.size === 0) {
    return { ok: false, code: "EMPTY_FILE", message: "That file is empty." };
  }

  // xlsx cannot be streamed for parsing, so it carries a tighter cap than the
  // PRD's CSV-oriented 200 MB.
  const limit = extension === ".xlsx" ? MAX_EXCEL_BYTES : MAX_UPLOAD_BYTES;
  if (file.size > limit) {
    const limitMb = Math.round(limit / 1024 / 1024);
    const sizeMb = (file.size / 1024 / 1024).toFixed(1);
    return {
      ok: false,
      code: "FILE_TOO_LARGE",
      message: `That file is ${sizeMb} MB; the limit for this format is ${limitMb} MB.`,
    };
  }

  return { ok: true };
}
