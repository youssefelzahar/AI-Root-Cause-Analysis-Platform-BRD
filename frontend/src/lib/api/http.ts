import { apiOrigin } from "./base-url";
import { ApiError } from "./errors";

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  timeoutMs?: number;
};

/**
 * The single fetch wrapper. Throws on any non-2xx; never returns fallback data.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, timeoutMs, headers, ...rest } = options;
  const method = (rest.method ?? "GET").toUpperCase();
  const url = `${apiOrigin()}${path}`;

  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");

  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    // Never set Content-Type for FormData - the browser must generate the
    // multipart boundary itself.
    payload = body;
  } else if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      ...rest,
      method,
      headers: requestHeaders,
      body: payload,
      cache: "no-store",
      signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : rest.signal,
    });
  } catch (cause) {
    // DNS failure, connection refused, CORS, abort.
    throw new ApiError({
      status: 0,
      code: "NETWORK_ERROR",
      message: "Cannot reach the API.",
      method,
      path,
      cause,
    });
  }

  const requestId = response.headers.get("x-request-id") ?? undefined;

  if (!response.ok) {
    let code = `HTTP_${response.status}`;
    let message = `Request failed with status ${response.status}.`;
    let details: unknown;

    try {
      const parsed = await response.json();
      if (parsed?.error) {
        code = parsed.error.code ?? code;
        message = parsed.error.message ?? message;
        details = parsed.error.details;
      } else if (typeof parsed?.detail === "string") {
        message = parsed.detail;
      }
    } catch {
      // Non-JSON error body; keep the status-derived message.
    }

    throw new ApiError({ status: response.status, code, message, method, path, details, requestId });
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
