/**
 * Typed API errors.
 *
 * The previous client caught everything and returned hardcoded fallback data,
 * so the UI looked healthy while the backend was down. Nothing here swallows:
 * every failure becomes an ApiError that a screen has to render.
 */

export type ProblemDetails = {
  code: string;
  message: string;
  details?: unknown;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly method: string;
  /** Path only. The request body is deliberately never captured - it can hold a SQL Server password. */
  readonly path: string;
  readonly details?: unknown;
  readonly requestId?: string;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    method: string;
    path: string;
    details?: unknown;
    requestId?: string;
    cause?: unknown;
  }) {
    super(init.message, { cause: init.cause });
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.method = init.method;
    this.path = init.path;
    this.details = init.details;
    this.requestId = init.requestId;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The backend returns 409 while profiling is still running. */
  get isNotReady(): boolean {
    return this.status === 409;
  }

  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

export function toUserMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isNetworkError) {
      return "Cannot reach the API. Check that the backend is running.";
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/** Field-level messages from a FastAPI 422 payload, keyed by field name. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.status !== 422) return {};
  const details = error.details;
  if (!Array.isArray(details)) return {};

  const result: Record<string, string> = {};
  for (const item of details) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as { location?: unknown[]; message?: string };
    const location = Array.isArray(entry.location) ? entry.location : [];
    const field = location.length ? String(location[location.length - 1]) : "_";
    if (entry.message) result[field] = entry.message;
  }
  return result;
}
