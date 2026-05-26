import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

// Phase 9 — playback subsystem. Single global queue lives in app
// state so navigation across pages doesn't reset the <video> element
// (PlaybackProvider mounts outside <Routes>).

export type ResolvedRangeItem = {
  type: "range";
  clip_id: string;
  source_id: string;
  source_filename: string;
  source_url: string;
  effective_start_sec: number;
  effective_end_sec: number;
};

export type TombstoneItem = {
  type: "tombstone";
  clip_id: string;
  reason: string;
};

export type ResolvedItem = ResolvedRangeItem | TombstoneItem;

type AttemptResolvedResponse = {
  attempt_id: string;
  items: ResolvedItem[];
};

type ClipPlayInput = {
  clip_id: string;
  source_id: string;
  source_filename: string;
  start_sec: number;
  end_sec: number;
};

type PlaybackState = {
  queue: ResolvedItem[];
  currentIndex: number;
  playing: boolean;
  dismissed: boolean;
  /** Optional label shown above the queue in the pane — e.g. "attempt: X". */
  queueLabel: string;
};

type PlaybackContextValue = PlaybackState & {
  playClip: (input: ClipPlayInput) => void;
  playAttempt: (attemptId: string, label?: string) => Promise<void>;
  pause: () => void;
  resume: () => void;
  dismiss: () => void;
  /** Called by PreviewPane when the current range finishes (timeupdate
   *  reached effective_end). Advances to next item or stops at queue end. */
  advance: () => void;
  /** Jump directly to a specific item in the queue (e.g., user clicks
   *  on a clip in the attempt's clip list). Skips tombstones forward. */
  seekToIndex: (i: number) => void;
};

const INITIAL_STATE: PlaybackState = {
  queue: [],
  currentIndex: -1,
  playing: false,
  dismissed: true,
  queueLabel: "",
};

const PlaybackContext = createContext<PlaybackContextValue | null>(null);

export function usePlayback(): PlaybackContextValue {
  const ctx = useContext(PlaybackContext);
  if (ctx == null) {
    throw new Error(
      "usePlayback() called outside <PlaybackProvider>. Wrap your app in App.tsx.",
    );
  }
  return ctx;
}

/** Find the next non-tombstone index >= `from`, or -1 if none. The
 *  pane handles tombstones via auto-advance after a 2s hold; this is
 *  for `seekToIndex` skipping. */
function findNextPlayable(queue: ResolvedItem[], from: number): number {
  for (let i = from; i < queue.length; i++) {
    if (queue[i].type === "range") return i;
  }
  return -1;
}

export function PlaybackProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PlaybackState>(INITIAL_STATE);

  const playClip = useCallback((input: ClipPlayInput) => {
    const range: ResolvedRangeItem = {
      type: "range",
      clip_id: input.clip_id,
      source_id: input.source_id,
      source_filename: input.source_filename,
      source_url: `/api/sources/${encodeURIComponent(input.source_id)}/video`,
      effective_start_sec: input.start_sec,
      effective_end_sec: input.end_sec,
    };
    setState({
      queue: [range],
      currentIndex: 0,
      playing: true,
      dismissed: false,
      queueLabel: `clip · ${input.source_filename}`,
    });
  }, []);

  const playAttempt = useCallback(
    async (attemptId: string, label?: string) => {
      try {
        const r = await fetch(
          `/api/attempts/${encodeURIComponent(attemptId)}/resolved`,
        );
        if (!r.ok) {
          console.warn(`playAttempt: /resolved returned ${r.status}`);
          return;
        }
        const body: AttemptResolvedResponse = await r.json();
        if (body.items.length === 0) {
          console.warn(`playAttempt: attempt ${attemptId} has no items`);
          return;
        }
        const first = findNextPlayable(body.items, 0);
        setState({
          queue: body.items,
          currentIndex: first >= 0 ? first : 0,
          playing: first >= 0,
          dismissed: false,
          queueLabel: label ?? `attempt #${attemptId}`,
        });
      } catch (e) {
        console.error("playAttempt failed:", e);
      }
    },
    [],
  );

  const pause = useCallback(() => {
    setState((s) => ({ ...s, playing: false }));
  }, []);

  const resume = useCallback(() => {
    setState((s) => ({ ...s, playing: s.currentIndex >= 0 }));
  }, []);

  const dismiss = useCallback(() => {
    setState(INITIAL_STATE);
  }, []);

  const advance = useCallback(() => {
    setState((s) => {
      const next = s.currentIndex + 1;
      if (next >= s.queue.length) {
        // End of queue — stop playing but keep the queue visible until dismissed.
        return { ...s, playing: false };
      }
      return { ...s, currentIndex: next, playing: s.queue[next].type === "range" };
    });
  }, []);

  const seekToIndex = useCallback((i: number) => {
    setState((s) => {
      if (i < 0 || i >= s.queue.length) return s;
      const target = findNextPlayable(s.queue, i);
      if (target < 0) return s;
      return { ...s, currentIndex: target, playing: true };
    });
  }, []);

  const value = useMemo<PlaybackContextValue>(
    () => ({
      ...state,
      playClip,
      playAttempt,
      pause,
      resume,
      dismiss,
      advance,
      seekToIndex,
    }),
    [state, playClip, playAttempt, pause, resume, dismiss, advance, seekToIndex],
  );

  return (
    <PlaybackContext.Provider value={value}>
      {children}
    </PlaybackContext.Provider>
  );
}
