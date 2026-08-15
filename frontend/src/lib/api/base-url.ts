/**
 * Isomorphic API origin.
 *
 * Browser -> "" so every request is same-origin (`/api/...`), handled by the
 * Next rewrite in dev and by nginx in the compose stack. Because no
 * `NEXT_PUBLIC_*` value is involved, nothing is inlined at build time and
 * there is no CORS to configure.
 *
 * Server (RSC / route handlers) -> an absolute origin read from a NON-public
 * env var at request time, so `docker compose` can change it without a
 * rebuild. `typeof window` is statically analysable, so this branch is
 * stripped from the client bundle and API_INTERNAL_URL can never leak.
 */
export function apiOrigin(): string {
  if (typeof window !== "undefined") return "";
  return process.env.API_INTERNAL_URL ?? "http://localhost:8000";
}
