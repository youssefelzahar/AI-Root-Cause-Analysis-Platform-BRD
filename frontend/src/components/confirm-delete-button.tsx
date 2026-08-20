"use client";

import { Loader2, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { toUserMessage } from "@/lib/api/errors";

/**
 * A delete control that confirms inline.
 *
 * Deliberately not `window.confirm`: it is blocked in some embedded contexts and
 * gives no room to name what is being deleted. The two-step inline swap makes
 * the destructive click impossible to hit by accident while keeping the row
 * layout intact.
 *
 * `onDelete` runs in the browser and talks to the API directly, matching the
 * rest of this app - there are no server actions here.
 */
export function ConfirmDeleteButton({
  onDelete,
  label = "Delete",
  confirmLabel = "Delete",
  redirectTo,
  iconOnly = false,
  disabled = false,
  disabledReason,
}: {
  onDelete: () => Promise<void>;
  /** Accessible name for the initial button. */
  label?: string;
  /** Text on the confirming button - name the thing where there is room. */
  confirmLabel?: string;
  /** Navigate here after a successful delete; otherwise refresh in place. */
  redirectTo?: string;
  iconOnly?: boolean;
  disabled?: boolean;
  /** Shown as the tooltip when disabled, so the block has a stated reason. */
  disabledReason?: string;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    setPending(true);
    setError(null);
    try {
      await onDelete();
      if (redirectTo) {
        router.push(redirectTo);
      } else {
        setConfirming(false);
      }
      // Refresh either way: the list this row belongs to is server-rendered.
      router.refresh();
    } catch (cause) {
      setError(toUserMessage(cause));
      setPending(false);
      setConfirming(false);
    }
  };

  if (error) {
    return (
      <div className="delete-inline">
        <span className="delete-error" role="alert">
          {error}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={() => setError(null)} type="button">
          Dismiss
        </button>
      </div>
    );
  }

  if (!confirming) {
    return (
      <button
        aria-label={label}
        className={iconOnly ? "btn btn-ghost btn-sm" : "btn btn-secondary btn-sm"}
        disabled={disabled || pending}
        onClick={() => setConfirming(true)}
        title={disabled ? disabledReason : label}
        type="button"
      >
        <Trash2 size={15} />
        {!iconOnly && label}
      </button>
    );
  }

  return (
    <div className="delete-inline">
      <button
        className="btn btn-danger btn-sm"
        disabled={pending}
        onClick={handleDelete}
        type="button"
      >
        {pending ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
        {pending ? "Deleting..." : confirmLabel}
      </button>
      <button
        className="btn btn-ghost btn-sm"
        disabled={pending}
        onClick={() => setConfirming(false)}
        type="button"
      >
        Cancel
      </button>
    </div>
  );
}
