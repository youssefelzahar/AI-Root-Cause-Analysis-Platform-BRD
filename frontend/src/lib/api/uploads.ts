import type { UploadResponse } from "@/types/api";

import { ApiError } from "./errors";

export type UploadProgress = { loaded: number; total: number };

/**
 * Upload with real progress.
 *
 * fetch() cannot report upload progress, so this uses XMLHttpRequest. It is a
 * single multipart request - no client-side chunking, because the backend
 * already streams the body in 1 MB chunks and enforces the size cap before
 * anything is buffered.
 */
export function uploadDatasetFile(
  file: File,
  options: {
    name?: string;
    onProgress?: (progress: UploadProgress) => void;
    signal?: AbortSignal;
  } = {},
): Promise<UploadResponse> {
  const { name, onProgress, signal } = options;

  return new Promise<UploadResponse>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (name) form.append("name", name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    // Never time out a 200 MB body client-side.
    xhr.timeout = 0;
    xhr.setRequestHeader("Accept", "application/json");

    const fail = (status: number, code: string, message: string, details?: unknown) =>
      reject(new ApiError({ status, code, message, method: "POST", path: "/api/uploads", details }));

    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.({ loaded: event.loaded, total: event.total });
    });

    xhr.addEventListener("load", () => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(xhr.responseText);
      } catch {
        parsed = undefined;
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as UploadResponse);
        return;
      }

      const body = parsed as { error?: { code?: string; message?: string; details?: unknown } };
      fail(
        xhr.status,
        body?.error?.code ?? `HTTP_${xhr.status}`,
        body?.error?.message ?? `Upload failed with status ${xhr.status}.`,
        body?.error?.details,
      );
    });

    xhr.addEventListener("error", () =>
      fail(0, "NETWORK_ERROR", "Cannot reach the API. Check that the backend is running."),
    );
    xhr.addEventListener("abort", () => fail(0, "UPLOAD_CANCELLED", "The upload was cancelled."));

    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}
