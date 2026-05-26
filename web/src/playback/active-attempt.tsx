import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

// Phase 10a — active attempt context. When the user is building or
// editing an attempt, this tracks which one their "+ add this clip"
// clicks should target. Persisted to localStorage so it survives
// reload; auto-clears when the attempt is deleted or belongs to a
// different project than the one being viewed.
//
// Lives next to PlaybackProvider in App.tsx so the take pages can
// dispatch reads/writes without prop-drilling.

const STORAGE_KEY = "clipfarm.active_attempt_id";

type ActiveAttemptContextValue = {
  /** Currently-active attempt id, or null. */
  activeAttemptId: string | null;
  /** Set the active attempt (or null to clear). Persists to localStorage. */
  setActiveAttemptId: (id: string | null) => void;
  /** Convenience: clear. */
  clear: () => void;
};

const ActiveAttemptContext = createContext<ActiveAttemptContextValue | null>(null);

export function useActiveAttempt(): ActiveAttemptContextValue {
  const ctx = useContext(ActiveAttemptContext);
  if (ctx == null) {
    throw new Error(
      "useActiveAttempt() called outside <ActiveAttemptProvider>. Wrap your app.",
    );
  }
  return ctx;
}

function loadStored(): string | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

export function ActiveAttemptProvider({ children }: { children: ReactNode }) {
  const [activeAttemptId, _setActiveAttemptIdState] = useState<string | null>(
    () => loadStored(),
  );

  const setActiveAttemptId = useCallback((id: string | null) => {
    _setActiveAttemptIdState(id);
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Quota / private mode — silent failure, in-memory state still works.
    }
  }, []);

  const clear = useCallback(() => setActiveAttemptId(null), [setActiveAttemptId]);

  return (
    <ActiveAttemptContext.Provider
      value={{ activeAttemptId, setActiveAttemptId, clear }}
    >
      {children}
    </ActiveAttemptContext.Provider>
  );
}

/**
 * Hook that periodically re-validates the active attempt against
 * server state — if it points at an attempt that's been deleted or
 * belongs to a different project, clear it. Called from the take
 * pages (Project + ScriptTOC + Attempts) so the context stays
 * consistent without each page re-implementing the check.
 */
export function useActiveAttemptValidation(
  attemptsState: Record<string, { project_id: string }> | null,
  currentProjectId: string | null,
) {
  const { activeAttemptId, clear } = useActiveAttempt();
  useEffect(() => {
    if (!activeAttemptId || !attemptsState) return;
    const att = attemptsState[activeAttemptId];
    if (att == null) {
      // Attempt was deleted — clear.
      clear();
      return;
    }
    if (currentProjectId && att.project_id !== currentProjectId) {
      // User switched to a different project; the active attempt
      // belongs elsewhere. Clear so + clicks don't go cross-project.
      clear();
    }
  }, [activeAttemptId, attemptsState, currentProjectId, clear]);
}
