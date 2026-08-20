import type { Notice } from "@/types/rca";

/**
 * Everything the engine had to decide or could not determine.
 *
 * These are not errors, and they are not decoration either: an excluded partial
 * period or an assumed period split changes what the numbers mean, so the
 * judgement calls are shown rather than buried.
 */
export function InvestigationNotices({ notices }: { notices: Notice[] }) {
  if (notices.length === 0) return null;

  return (
    <ul className="issue-list">
      {notices.map((notice) => (
        <li className={`issue ${notice.severity}`} key={notice.code}>
          <p className="issue-code">{notice.code}</p>
          <p>{notice.message}</p>
        </li>
      ))}
    </ul>
  );
}
