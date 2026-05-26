import { ReactNode } from "react";

// Phase 9 kickoff carry — shell-only extraction of the right-side
// detail panel used on Project, ScriptTOC, and Attempts. Each page
// duplicated this chrome (sticky positioning + max-height + scroll +
// header row with close-X + body container) since Phase 7; the third-
// use rule fires now.
//
// Page-specific BODIES stay inline — the transcript/clip-list/etc.
// don't share enough shape to abstract. Per the Phase 9 plan-review:
// "shell only, not full pattern unification."

interface SidePanelProps {
  /** When false, renders the dim empty-state with `emptyMessage`.
   *  When true, renders the full panel with `header` + `children` body. */
  open: boolean;
  /** Empty-state text shown when nothing is selected. */
  emptyMessage: string;
  /** Called when the close-X button is clicked. */
  onClose: () => void;
  /** Left-of-row header content (title + subtitle area). Truncates
   *  if too wide; the close-X stays anchored on the right. */
  header: ReactNode;
  /** Body slot rendered below the header. Use a fragment for vertical
   *  flow; the shell handles vertical spacing via `space-y-3`. */
  children: ReactNode;
}

export function SidePanel({
  open,
  emptyMessage,
  onClose,
  header,
  children,
}: SidePanelProps) {
  if (!open) {
    return (
      <aside className="rounded-md border border-neutral-800 bg-neutral-950/60 p-4 text-sm text-neutral-500">
        {emptyMessage}
      </aside>
    );
  }
  return (
    <aside className="rounded-md border border-neutral-800 bg-neutral-950/80 p-4 space-y-3 sticky top-4 max-h-[calc(100vh-7rem)] overflow-y-auto">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">{header}</div>
        <button
          onClick={onClose}
          className="text-neutral-500 hover:text-white text-xs px-1.5 py-0.5 rounded hover:bg-neutral-800"
          aria-label="Close detail panel"
        >
          ✕
        </button>
      </div>
      {children}
    </aside>
  );
}
