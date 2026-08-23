"use client";

import { X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";

/**
 * A modal built on the native `<dialog>` element.
 *
 * Its own file, never `components/ui/index.tsx`: the `"use client"` directive is
 * per-file, and that barrel exports Panel, Alert, DefinitionList and the
 * skeletons, which every page renders on the server. Adding the directive there
 * would quietly move the whole app's chrome into the client bundle.
 *
 * Native `<dialog>` rather than a portal: focus trapping, Escape, the backdrop
 * and top-layer stacking all come from the element, leaving showModal()/close()
 * as the only JavaScript. `--z-dialog` has been reserved in tokens.css since
 * Phase 1 with nothing to use it.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  size = "md",
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** "lg" for anything holding SQL. */
  size?: "md" | "lg";
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    // Guarded on element.open: calling showModal() on an already-open dialog
    // throws InvalidStateError.
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={size === "lg" ? "dialog dialog-lg" : "dialog"}
      aria-labelledby={headingId}
      aria-describedby={description ? descriptionId : undefined}
      // Escape fires onCancel and native close fires onClose. Both are wired so
      // React state can never drift from the DOM's own open state.
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
      // Closes only when the backdrop itself is clicked; .dialog-panel swallows
      // clicks inside the content.
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="dialog-panel">
        <div className="dialog-head">
          <div>
            <h2 id={headingId}>{title}</h2>
            {description ? (
              <p id={descriptionId} className="muted">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        {/* Unmounted while closed: a query trace can hold a lot of SQL, and
            keeping it mounted would defeat loading it lazily in the first
            place. */}
        <div className="dialog-body">{open ? children : null}</div>

        {footer ? <div className="dialog-foot">{footer}</div> : null}
      </div>
    </dialog>
  );
}
