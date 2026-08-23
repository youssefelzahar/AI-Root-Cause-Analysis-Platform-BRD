import type { Notice } from "@/types/rca";

/**
 * Everything the engine had to decide or could not determine.
 *
 * These are not errors, and they are not decoration either: an excluded partial
 * period or an assumed period split changes what the numbers mean, so the
 * judgement calls are shown rather than buried.
 *
 * The code is not a unique key. Some notices are emitted once per dimension -
 * DIMENSION_TRUNCATED names each truncated dimension separately, which is the
 * point of it - so the index goes into the key, as it does everywhere else this
 * list shape is rendered.
 */
export function InvestigationNotices({ notices }: { notices: Notice[] }) {
  if (notices.length === 0) return null;

  return (
    <ul className="issue-list">
      {notices.map((notice, index) => (
        <li className={`issue ${notice.severity}`} key={`${notice.code}-${index}`}>
          <p className="issue-code">{notice.code}</p>
          <p>{notice.message}</p>
        </li>
      ))}
    </ul>
  );
}
