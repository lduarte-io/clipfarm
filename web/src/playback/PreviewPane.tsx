import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { ResolvedRangeItem, usePlayback } from "./context";

// Phase 9 — floating bottom-right preview pane.
//
// Two alternating <video> elements (A / B) swap on range-end.
// One is visible at a time; the other preloads the next range so the
// same-source swap is gapless. Cross-source swaps need the browser to
// fetch a new file's metadata — for that case we hold the just-
// finished frame in place and show a "↻ Loading next clip…" overlay
// until the new element's `canplay` event fires.
//
// End-of-range detection is `timeupdate` comparing currentTime against
// the range's `effective_end_sec` with a 50ms tolerance — the native
// `ended` event won't fire when we trim before file-end.
//
// Native <video> controls={false} — we use custom controls so the
// native scrubber can't seek out of the resolved range.

// Tuning knob — how far ahead of the swap moment we tell the hidden
// element to load the next range's source + seek to its
// effective_start. Too short = swap stalls on cross-source. Too long
// = wasted preloads when user pauses or dismisses. 0.5s feels right
// for same-source SSD reads + ~100-300ms cross-source latency.
const PRELOAD_AHEAD_SEC = 0.5;
const END_TOLERANCE_SEC = 0.05;

const DEFAULT_SIZE = { width: 480, height: 270 };
const MIN_SIZE = { width: 320, height: 180 };
const SIZE_STORAGE_KEY = "clipfarm.preview_pane_size";

// 2-second hold on tombstone items per the plan.
const TOMBSTONE_HOLD_MS = 2000;

function formatTimestamp(sec: number): string {
  const total = Math.floor(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

type Size = { width: number; height: number };

function loadStoredSize(): Size {
  try {
    const raw = localStorage.getItem(SIZE_STORAGE_KEY);
    if (raw == null) return DEFAULT_SIZE;
    const parsed = JSON.parse(raw);
    if (
      typeof parsed.width !== "number"
      || typeof parsed.height !== "number"
      || parsed.width < MIN_SIZE.width
      || parsed.height < MIN_SIZE.height
    ) {
      return DEFAULT_SIZE;
    }
    return { width: parsed.width, height: parsed.height };
  } catch {
    return DEFAULT_SIZE;
  }
}

function clampSize(size: Size): Size {
  const maxW = Math.floor(window.innerWidth * 0.8);
  const maxH = Math.floor(window.innerHeight * 0.8);
  return {
    width: Math.max(MIN_SIZE.width, Math.min(maxW, size.width)),
    height: Math.max(MIN_SIZE.height, Math.min(maxH, size.height)),
  };
}

export function PreviewPane() {
  const {
    queue,
    currentIndex,
    playing,
    dismissed,
    queueLabel,
    pause,
    resume,
    dismiss,
    advance,
  } = usePlayback();

  const [minimized, setMinimized] = useState(false);
  const [size, setSize] = useState<Size>(() => loadStoredSize());
  const [crossSourceLoading, setCrossSourceLoading] = useState(false);

  // Two video elements; refs swap roles as we step through the queue.
  // `activeIdx` is which ref (0 or 1) is currently the visible/playing
  // element; the other is the hidden preloader.
  const videoARef = useRef<HTMLVideoElement | null>(null);
  const videoBRef = useRef<HTMLVideoElement | null>(null);
  const [activeIdx, setActiveIdx] = useState<0 | 1>(0);

  const currentItem = currentIndex >= 0 ? queue[currentIndex] : null;
  const currentRange: ResolvedRangeItem | null =
    currentItem?.type === "range" ? currentItem : null;
  const nextItem =
    currentIndex >= 0 && currentIndex + 1 < queue.length
      ? queue[currentIndex + 1]
      : null;
  const nextRange: ResolvedRangeItem | null =
    nextItem?.type === "range" ? nextItem : null;

  // Persist size changes to localStorage.
  useEffect(() => {
    try {
      localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify(size));
    } catch {
      // Ignore quota errors.
    }
  }, [size]);

  // ─────────────────────────────────────────────────────────────────────
  // Tombstone hold: if currentItem is a tombstone, wait 2s then advance.
  // ─────────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (currentItem?.type !== "tombstone") return;
    const handle = setTimeout(() => advance(), TOMBSTONE_HOLD_MS);
    return () => clearTimeout(handle);
  }, [currentItem, advance]);

  // ─────────────────────────────────────────────────────────────────────
  // Active video element setup — set src + seek when currentRange changes.
  // ─────────────────────────────────────────────────────────────────────
  const activeRef = activeIdx === 0 ? videoARef : videoBRef;
  const hiddenRef = activeIdx === 0 ? videoBRef : videoARef;

  useEffect(() => {
    const v = activeRef.current;
    if (!v || !currentRange) return;
    setCrossSourceLoading(false);
    // If the active element's src doesn't match the current range's
    // source URL, load it. This is the cross-source path (or first-load).
    if (v.currentSrc.split("/api/")[1] !== currentRange.source_url.split("/api/")[1]) {
      setCrossSourceLoading(true);
      v.src = currentRange.source_url;
      // canplay fires when we can start playback at currentTime.
      const onCanPlay = () => {
        v.currentTime = currentRange.effective_start_sec;
        setCrossSourceLoading(false);
        if (playing) v.play().catch(() => {});
      };
      v.addEventListener("canplay", onCanPlay, { once: true });
      v.load();
      return () => v.removeEventListener("canplay", onCanPlay);
    }
    // Same-source seek.
    v.currentTime = currentRange.effective_start_sec;
    if (playing) v.play().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRange?.source_url, currentRange?.effective_start_sec, currentIndex]);

  // ─────────────────────────────────────────────────────────────────────
  // Play / pause from playback state.
  // ─────────────────────────────────────────────────────────────────────
  useEffect(() => {
    const v = activeRef.current;
    if (!v) return;
    if (playing && currentRange) {
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }, [playing, currentRange, activeRef]);

  // ─────────────────────────────────────────────────────────────────────
  // Preload next range on the hidden element when PRELOAD_AHEAD_SEC of
  // remaining time on the current range. Same-source = seek; cross-
  // source = swap src + load (which the next-range effect handles when
  // we eventually swap roles).
  // ─────────────────────────────────────────────────────────────────────
  const preloadedKeyRef = useRef<string | null>(null);

  const onTimeUpdate = useCallback(() => {
    const v = activeRef.current;
    const hv = hiddenRef.current;
    if (!v || !currentRange) return;

    // Range-end detection.
    if (v.currentTime + END_TOLERANCE_SEC >= currentRange.effective_end_sec) {
      v.pause();
      preloadedKeyRef.current = null;
      // Swap roles. The hidden element should already have the next
      // range preloaded (same-source path); cross-source needs the
      // next-range effect to kick in after the swap.
      if (nextRange && hv && hv.currentSrc.split("/api/")[1] === nextRange.source_url.split("/api/")[1]) {
        // Same-source: just swap.
        setActiveIdx((idx) => (idx === 0 ? 1 : 0));
      }
      // For cross-source or no-next-range, just advance — the
      // active-ref effect picks up the new currentRange on re-render.
      advance();
      return;
    }

    // Preload-ahead: when within PRELOAD_AHEAD_SEC of the end, set up
    // the hidden element for the next range.
    if (!hv || !nextRange) return;
    const remaining = currentRange.effective_end_sec - v.currentTime;
    if (remaining > PRELOAD_AHEAD_SEC) return;
    const key = `${nextRange.source_url}@${nextRange.effective_start_sec.toFixed(3)}`;
    if (preloadedKeyRef.current === key) return;
    preloadedKeyRef.current = key;
    if (hv.currentSrc.split("/api/")[1] === nextRange.source_url.split("/api/")[1]) {
      // Same-source: just seek (much faster than reloading).
      hv.currentTime = nextRange.effective_start_sec;
    } else {
      // Cross-source: load the new file in the hidden element. When we
      // actually swap, we'll need to wait for canplay (next-range
      // effect handles this).
      hv.src = nextRange.source_url;
      hv.load();
    }
  }, [activeRef, hiddenRef, currentRange, nextRange, advance]);

  // ─────────────────────────────────────────────────────────────────────
  // Drag-resize handle (top-left corner, since pane is anchored BR).
  // ─────────────────────────────────────────────────────────────────────
  const dragState = useRef<{
    startX: number;
    startY: number;
    startW: number;
    startH: number;
  } | null>(null);

  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragState.current = {
        startX: e.clientX,
        startY: e.clientY,
        startW: size.width,
        startH: size.height,
      };
      const onMove = (ev: MouseEvent) => {
        const d = dragState.current;
        if (!d) return;
        // Pane is anchored bottom-right. Dragging the top-left corner
        // up/left should INCREASE size. So delta is negative of the
        // mouse movement.
        const dx = d.startX - ev.clientX;
        const dy = d.startY - ev.clientY;
        setSize(clampSize({
          width: d.startW + dx,
          height: d.startH + dy,
        }));
      };
      const onUp = () => {
        dragState.current = null;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [size.width, size.height],
  );

  // ─────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────

  if (dismissed || currentIndex < 0) return null;

  if (minimized) {
    return (
      <div
        className="fixed bottom-3 right-3 z-50 rounded-full bg-neutral-900 border border-neutral-700 shadow-lg px-3 py-1.5 flex items-center gap-2 text-xs"
      >
        <button
          onClick={() => setMinimized(false)}
          className="text-neutral-300 hover:text-white"
        >
          ▶ {queueLabel || "playback"}
        </button>
        <button
          onClick={dismiss}
          className="text-neutral-500 hover:text-white"
          aria-label="Dismiss preview"
        >
          ✕
        </button>
      </div>
    );
  }

  const label = currentRange
    ? `${currentIndex + 1} of ${queue.length} · ${currentRange.source_filename} · ${formatTimestamp(currentRange.effective_start_sec)}–${formatTimestamp(currentRange.effective_end_sec)}`
    : currentItem?.type === "tombstone"
      ? `${currentIndex + 1} of ${queue.length} · ▢ Removed clip`
      : "playback";

  return (
    <div
      className="fixed bottom-3 right-3 z-50 rounded-md border border-neutral-700 bg-neutral-950 shadow-xl overflow-hidden flex flex-col"
      style={{ width: size.width, height: size.height }}
    >
      {/* Resize handle on the top-left corner (only growable corner). */}
      <div
        onMouseDown={onResizeStart}
        className="absolute top-0 left-0 w-3 h-3 cursor-nwse-resize bg-neutral-700 hover:bg-neutral-500 z-20"
        title="Drag to resize"
      />
      <div className="relative flex-1 bg-black">
        {/* Two alternating <video> elements; activeIdx toggles which is visible. */}
        <video
          ref={videoARef}
          controls={false}
          className={`absolute inset-0 w-full h-full object-contain ${activeIdx === 0 ? "" : "hidden"}`}
          onTimeUpdate={activeIdx === 0 ? onTimeUpdate : undefined}
          preload="auto"
        />
        <video
          ref={videoBRef}
          controls={false}
          className={`absolute inset-0 w-full h-full object-contain ${activeIdx === 1 ? "" : "hidden"}`}
          onTimeUpdate={activeIdx === 1 ? onTimeUpdate : undefined}
          preload="auto"
        />
        {currentItem?.type === "tombstone" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-neutral-950 text-center p-4">
            <div className="text-2xl mb-2">▢</div>
            <div className="text-sm text-neutral-300">Removed clip</div>
            <div className="text-xs text-neutral-500 mt-1">
              Pick a replacement on the Attempts page
            </div>
          </div>
        )}
        {crossSourceLoading && (
          <div className="absolute top-1 right-1 rounded bg-neutral-900/80 px-2 py-1 text-[10px] text-neutral-300">
            ↻ Loading next clip…
          </div>
        )}
      </div>
      <div className="border-t border-neutral-800 px-2 py-1.5 flex items-center gap-2 text-xs bg-neutral-900">
        <button
          onClick={playing ? pause : resume}
          className="text-neutral-200 hover:text-white px-1"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "⏸" : "▶"}
        </button>
        <span className="flex-1 min-w-0 truncate text-neutral-400 font-mono text-[10px]">
          {label}
        </span>
        <button
          onClick={() => setMinimized(true)}
          className="text-neutral-500 hover:text-white px-1"
          aria-label="Minimize"
          title="Minimize"
        >
          —
        </button>
        <button
          onClick={dismiss}
          className="text-neutral-500 hover:text-white px-1"
          aria-label="Dismiss"
          title="Close preview"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
