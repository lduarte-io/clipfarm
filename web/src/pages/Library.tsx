import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

// Phase 4 adds boundary correction: split / merge / adjust / create /
// delete operations against the transcript view. Multi-clip selection via
// cmd-click; action bar shows the available ops; Cmd+Backspace + always-
// confirm dialog for delete.

type Source = {
  filename: string;
  path: string;
  duration_sec: number | null;
  fps: number | null;
  transcript_path: string | null;
  added_at: string;
  unavailable: boolean;
};

type IngestRejection = {
  filename: string;
  reason: string;
  sanitized_rename: string | null;
  detail: string;
};

type IngestResult = {
  sources_added: string[];
  sources_skipped: string[];
  sources_updated: string[];
  rejected: IngestRejection[];
  warnings: string[];
  clips_detected: number;
};

type WhisperWord = { start: number; end: number; word: string };
type WhisperSegment = { id?: number; start: number; end: number; text?: string; words: WhisperWord[] };
type ClipRange = { clip_id: string; start_sec: number; end_sec: number };
type TranscriptViewData = {
  source_id: string;
  filename: string;
  duration_sec: number | null;
  segments: WhisperSegment[];
  clips: ClipRange[];
};

type Hit = {
  source_id: string;
  filename: string;
  clip_id: string | null;
  word_index: number;
  timestamp_sec: number;
  context_before: string;
  match: string;
  context_after: string;
};

type SearchResponse = {
  query: string;
  total: number;
  truncated: boolean;
  hits: Hit[];
};

type AppState = {
  version: number;
  sources: Record<string, Source>;
  clips: Record<string, { source_id: string; start_sec: number; end_sec: number }>;
};

function formatDuration(sec: number | null): string {
  if (sec == null) return "—";
  const total = Math.round(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}h${String(m).padStart(2, "0")}m`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function formatTimestamp(sec: number): string {
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

const CLIP_TINTS = ["bg-neutral-800/60", "bg-neutral-700/60"];

// ───────────────────────────────────────────────────────────────────────────
// API helpers — small wrappers around the boundary routes. Each rejects on
// non-2xx and returns the parsed body.
// ───────────────────────────────────────────────────────────────────────────

async function callJson(url: string, init: RequestInit): Promise<unknown> {
  const r = await fetch(url, init);
  if (!r.ok) {
    const text = await r.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = typeof parsed.detail === "string" ? parsed.detail : text;
    } catch {}
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

const api = {
  split: (clipId: string, splitAtSec: number) =>
    callJson(`/api/clips/${encodeURIComponent(clipId)}/split`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ split_at_sec: splitAtSec }),
    }),
  merge: (clipIds: string[]) =>
    callJson(`/api/clips/merge`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ clip_ids: clipIds }),
    }),
  adjust: (clipId: string, startSec: number, endSec: number) =>
    callJson(`/api/clips/${encodeURIComponent(clipId)}/boundaries`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ start_sec: startSec, end_sec: endSec }),
    }),
  create: (sourceId: string, startSec: number, endSec: number) =>
    callJson(`/api/sources/${encodeURIComponent(sourceId)}/clips`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ start_sec: startSec, end_sec: endSec }),
    }),
  remove: (clipId: string) =>
    callJson(`/api/clips/${encodeURIComponent(clipId)}`, {
      method: "DELETE",
    }),
};

// ───────────────────────────────────────────────────────────────────────────
// Ingest panel
// ───────────────────────────────────────────────────────────────────────────

function IngestPanel({ onAfterIngest }: { onAfterIngest: () => void }) {
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<IngestResult | null>(null);

  async function runIngest() {
    if (!folder.trim()) {
      setError("Enter an absolute folder path.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/ingest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText }));
        setError(typeof body.detail === "string" ? body.detail : JSON.stringify(body));
        return;
      }
      const result: IngestResult = await r.json();
      setLastResult(result);
      onAfterIngest();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-md border border-neutral-800 bg-neutral-900/50" open>
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium select-none">
        Ingest
      </summary>
      <div className="p-3 space-y-2 border-t border-neutral-800">
        <input
          type="text"
          className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-xs font-mono"
          placeholder="/abs/path/to/folder"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          disabled={busy}
          spellCheck={false}
        />
        <button
          onClick={runIngest}
          disabled={busy}
          className="w-full rounded-md bg-white text-neutral-950 font-medium px-3 py-1.5 text-xs hover:bg-neutral-200 disabled:opacity-50"
        >
          {busy ? "Ingesting…" : "Ingest folder"}
        </button>
        {error && <div className="text-red-400 text-xs whitespace-pre-wrap">{error}</div>}
        {lastResult && (
          <div className="text-xs space-y-1 pt-2 border-t border-neutral-800">
            <div className="text-neutral-300">
              <span className="text-green-400">+{lastResult.sources_added.length}</span>
              {" · "}
              <span className="text-blue-400">↑{lastResult.sources_updated.length}</span>
              {" · "}
              <span className="text-neutral-500">={lastResult.sources_skipped.length}</span>
              {" · "}
              <span className="text-neutral-300">{lastResult.clips_detected} clips</span>
            </div>
            {lastResult.sources_skipped.length > 0 && (
              <details>
                <summary className="text-neutral-500 cursor-pointer text-xs">
                  {lastResult.sources_skipped.length} skipped
                </summary>
                <ul className="mt-1 ml-4 list-disc text-neutral-500 text-xs">
                  {lastResult.sources_skipped.map((n) => (
                    <li key={n}><code>{n}</code></li>
                  ))}
                </ul>
              </details>
            )}
            {lastResult.rejected.length > 0 && (
              <details>
                <summary className="text-amber-400 cursor-pointer text-xs">
                  {lastResult.rejected.length} rejected
                </summary>
                <ul className="mt-1 ml-4 list-disc text-amber-200 text-xs space-y-1">
                  {lastResult.rejected.map((r) => (
                    <li key={r.filename}>
                      <code>{r.filename}</code> — {r.reason}
                      {r.sanitized_rename && (
                        <> → <code>{r.sanitized_rename}</code></>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {lastResult.warnings.length > 0 && (
              <details>
                <summary className="text-neutral-400 cursor-pointer text-xs">
                  {lastResult.warnings.length} warning
                  {lastResult.warnings.length === 1 ? "" : "s"}
                </summary>
                <ul className="mt-1 ml-4 list-disc text-neutral-400 text-xs">
                  {lastResult.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </div>
    </details>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Confirm dialog
// ───────────────────────────────────────────────────────────────────────────

function ConfirmDialog({
  title,
  body,
  confirmLabel,
  destructive,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter") onConfirm();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel, onConfirm]);

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
      <div className="bg-neutral-900 border border-neutral-700 rounded-md p-5 max-w-md w-full mx-4 space-y-3">
        <h3 className="font-semibold">{title}</h3>
        <p className="text-sm text-neutral-300 whitespace-pre-wrap">{body}</p>
        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm rounded-md border border-neutral-700 hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-3 py-1.5 text-sm rounded-md font-medium ${
              destructive
                ? "bg-red-600 text-white hover:bg-red-700"
                : "bg-white text-neutral-950 hover:bg-neutral-200"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Create-clip dialog (numeric range entry — works on footage-only sources too)
// ───────────────────────────────────────────────────────────────────────────

type TimeMode = "mmss" | "seconds";

/** Parse a string in the given mode → seconds (number) or null on
 *  malformed input.
 *  - "mmss": accepts "M:SS", "MM:SS.sss", "H:MM:SS", "12" (treated
 *    as 12 seconds when no colon present — graceful fallback so the
 *    user can paste raw seconds even in mm:ss mode).
 *  - "seconds": parseFloat. */
function parseTimeInput(s: string, mode: TimeMode): number | null {
  const trimmed = s.trim();
  if (!trimmed) return null;
  if (mode === "seconds") {
    const n = parseFloat(trimmed);
    return Number.isFinite(n) ? n : null;
  }
  // mmss mode. Split on `:`. Walk right-to-left: last = seconds,
  // next = minutes, next = hours. Each component is a non-negative
  // number; only the last can have decimals.
  const parts = trimmed.split(":");
  if (parts.length === 1) {
    // No colon — accept as raw seconds (be forgiving).
    const n = parseFloat(parts[0]);
    return Number.isFinite(n) ? n : null;
  }
  if (parts.length > 3) return null;
  let total = 0;
  for (let i = 0; i < parts.length; i++) {
    const isLast = i === parts.length - 1;
    const part = parts[i].trim();
    if (!part) return null;
    const n = isLast ? parseFloat(part) : parseInt(part, 10);
    if (!Number.isFinite(n) || n < 0) return null;
    if (!isLast && /\./.test(part)) return null; // only last segment may have decimals
    const placeMultiplier = Math.pow(60, parts.length - 1 - i);
    total += n * placeMultiplier;
  }
  return total;
}

/** Format a number-of-seconds for display in the given mode. */
function formatTimeForMode(n: number, mode: TimeMode): string {
  if (mode === "seconds") {
    // Three decimals matches the input step.
    return n.toFixed(3).replace(/\.?0+$/, "");
  }
  if (n < 0) return "0:00";
  const totalMs = Math.round(n * 1000);
  const ms = totalMs % 1000;
  const totalSec = Math.floor(totalMs / 1000);
  const s = totalSec % 60;
  const m = Math.floor(totalSec / 60) % 60;
  const h = Math.floor(totalSec / 3600);
  const ssms = ms > 0
    ? `${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0").replace(/0+$/, "")}`
    : String(s).padStart(2, "0");
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${ssms}`
    : `${m}:${ssms}`;
}

function CreateClipDialog({
  sourceId,
  duration,
  onClose,
  onCreated,
}: {
  sourceId: string;
  duration: number | null;
  onClose: () => void;
  onCreated: (newId: string) => void;
}) {
  const [mode, setMode] = useState<TimeMode>("mmss");
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Switching modes: convert the current parsed value to the new
  // mode's string representation so the user doesn't lose entered
  // numbers. Empty fields stay empty.
  const switchMode = (next: TimeMode) => {
    if (next === mode) return;
    const s = parseTimeInput(startInput, mode);
    const e = parseTimeInput(endInput, mode);
    setStartInput(s != null ? formatTimeForMode(s, next) : startInput);
    setEndInput(e != null ? formatTimeForMode(e, next) : endInput);
    setMode(next);
  };

  async function submit() {
    const s = parseTimeInput(startInput, mode);
    const e = parseTimeInput(endInput, mode);
    if (s == null || e == null) {
      setError(
        mode === "mmss"
          ? "Both fields must parse as M:SS, M:SS.sss, H:MM:SS, or raw seconds."
          : "Both fields must be numbers (seconds).",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = (await api.create(sourceId, s, e)) as { new_clip_id: string };
      onCreated(body.new_clip_id);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  const labelStart = mode === "mmss" ? "Start (M:SS)" : "Start (seconds)";
  const labelEnd = mode === "mmss" ? "End (M:SS)" : "End (seconds)";
  const placeholderStart = mode === "mmss" ? "e.g. 0:45 or 1:23.500" : "e.g. 45.0";
  const placeholderEnd = mode === "mmss" ? "e.g. 0:59" : "e.g. 59.0";

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
      <div className="bg-neutral-900 border border-neutral-700 rounded-md p-5 max-w-md w-full mx-4 space-y-3">
        <h3 className="font-semibold">Create clip</h3>
        <p className="text-sm text-neutral-400">
          Enter the start and end timestamps. Overlapping an existing clip on
          the same source is allowed (you may have already grabbed the same
          range for a different purpose).
          {duration != null && ` Source duration: ${formatTimestamp(duration)}.`}
        </p>
        {/* Phase 10a dogfood addition: input-format toggle. */}
        <div className="flex items-center gap-2 text-xs">
          <span className="text-neutral-500">Input format:</span>
          <div className="inline-flex rounded-md border border-neutral-700 overflow-hidden">
            <button
              type="button"
              onClick={() => switchMode("mmss")}
              className={`px-2 py-1 text-xs ${
                mode === "mmss"
                  ? "bg-neutral-200 text-neutral-950 font-medium"
                  : "bg-neutral-900 text-neutral-300 hover:bg-neutral-800"
              }`}
              disabled={busy}
            >
              M:SS
            </button>
            <button
              type="button"
              onClick={() => switchMode("seconds")}
              className={`px-2 py-1 text-xs ${
                mode === "seconds"
                  ? "bg-neutral-200 text-neutral-950 font-medium"
                  : "bg-neutral-900 text-neutral-300 hover:bg-neutral-800"
              }`}
              disabled={busy}
            >
              seconds
            </button>
          </div>
        </div>
        <div className="space-y-2">
          <label className="block text-xs">
            {labelStart}
            <input
              autoFocus
              type="text"
              value={startInput}
              onChange={(e) => setStartInput(e.target.value)}
              placeholder={placeholderStart}
              className="block w-full mt-1 rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm font-mono"
              disabled={busy}
              autoComplete="off"
            />
          </label>
          <label className="block text-xs">
            {labelEnd}
            <input
              type="text"
              value={endInput}
              onChange={(e) => setEndInput(e.target.value)}
              placeholder={placeholderEnd}
              className="block w-full mt-1 rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm font-mono"
              disabled={busy}
              autoComplete="off"
            />
          </label>
        </div>
        {error && <div className="text-red-400 text-xs whitespace-pre-wrap">{error}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md border border-neutral-700 hover:bg-neutral-800"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-white text-neutral-950 font-medium hover:bg-neutral-200"
          >
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Transcript view — adds multi-clip selection + action bar + ops.
// ───────────────────────────────────────────────────────────────────────────

type FocusRequest = { wordIndex: number; nonce: number };

function TranscriptView({
  sourceId,
  focus,
  refreshNonce,
  pushToast,
  onAfterMutation,
}: {
  sourceId: string | null;
  focus: FocusRequest | null;
  refreshNonce: number;
  pushToast: (kind: "ok" | "err", text: string) => void;
  onAfterMutation: () => void;
}) {
  const [data, setData] = useState<TranscriptViewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedClips, setSelectedClips] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Fetch / refetch the transcript when source or refreshNonce changes.
  useEffect(() => {
    setData(null);
    setError(null);
    setSelectedClips(new Set());
    if (!sourceId) return;
    let cancelled = false;
    (async () => {
      const r = await fetch(`/api/sources/${encodeURIComponent(sourceId)}/transcript`);
      if (cancelled) return;
      if (r.status === 422) {
        setError("This source has no transcript yet (footage-only). You can still create clips by numeric range.");
        return;
      }
      if (!r.ok) {
        setError(`Failed to load transcript: ${r.status} ${r.statusText}`);
        return;
      }
      const body: TranscriptViewData = await r.json();
      setData(body);
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceId, refreshNonce]);

  const flatWords = useMemo(() => {
    if (!data) return [];
    const out: Array<{ idx: number; word: WhisperWord; clipId: string | null }> = [];
    let i = 0;
    for (const seg of data.segments) {
      for (const w of seg.words) {
        const clip = data.clips.find(
          (c) => w.start >= c.start_sec && w.start < c.end_sec
        );
        out.push({ idx: i, word: w, clipId: clip?.clip_id ?? null });
        i++;
      }
    }
    return out;
  }, [data?.segments, data?.clips]);

  // Search-result focus jump.
  useEffect(() => {
    if (!focus || !containerRef.current) return;
    const el = containerRef.current.querySelector(
      `[data-word-index="${focus.wordIndex}"]`
    ) as HTMLElement | null;
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const clipId = el.getAttribute("data-clip-id");
    if (clipId) {
      setSelectedClips(new Set([clipId]));
    }
    el.classList.add("ring-2", "ring-amber-400");
    const tid = setTimeout(() => {
      el.classList.remove("ring-2", "ring-amber-400");
    }, 1600);
    return () => clearTimeout(tid);
  }, [focus]);

  // ─── Selected-clip derived values ────────────────────────────────────────
  const selectedClipMeta = useMemo(() => {
    if (!data) return [];
    const ids = Array.from(selectedClips);
    return data.clips.filter((c) => selectedClips.has(c.clip_id));
  }, [data, selectedClips]);

  const singleSelected = selectedClipMeta.length === 1 ? selectedClipMeta[0] : null;

  // ─── Op wrappers — refresh state + transcript on success ────────────────
  const runMutation = useCallback(
    async (label: string, fn: () => Promise<unknown>) => {
      try {
        await fn();
        pushToast("ok", label);
        onAfterMutation();
      } catch (e) {
        pushToast("err", `${label} failed: ${String(e)}`);
      }
    },
    [pushToast, onAfterMutation]
  );

  // ─── Action handlers ─────────────────────────────────────────────────────
  const doSplit = useCallback(() => {
    if (!singleSelected) return;
    const mid = (singleSelected.start_sec + singleSelected.end_sec) / 2;
    runMutation("Split", async () => {
      const body = (await api.split(singleSelected.clip_id, mid)) as {
        new_clip_ids: [string, string];
      };
      setSelectedClips(new Set(body.new_clip_ids));
    });
  }, [singleSelected, runMutation]);

  const doMerge = useCallback(() => {
    const ids = Array.from(selectedClips);
    if (ids.length < 2) return;
    runMutation(`Merge ${ids.length} clips`, async () => {
      const body = (await api.merge(ids)) as { new_clip_id: string };
      setSelectedClips(new Set([body.new_clip_id]));
    });
  }, [selectedClips, runMutation]);

  // Word-boundary nudge helpers for extend/shrink.
  const nudge = useCallback(
    (side: "start" | "end", dir: "out" | "in") => {
      if (!singleSelected || !data) return;
      const clip = singleSelected;
      // Find current word boundaries on either side of the current edge.
      const wordEdges = flatWords.map((fw) => fw.word.start).concat(
        flatWords.length ? [flatWords[flatWords.length - 1].word.end] : []
      );
      let newStart = clip.start_sec;
      let newEnd = clip.end_sec;
      if (side === "start") {
        // "out" → earlier word boundary; "in" → later.
        const sorted = [...wordEdges].sort((a, b) => a - b);
        if (dir === "out") {
          const prev = [...sorted].reverse().find((t) => t < clip.start_sec);
          if (prev !== undefined) newStart = prev;
        } else {
          const next = sorted.find((t) => t > clip.start_sec && t < clip.end_sec);
          if (next !== undefined) newStart = next;
        }
      } else {
        const sorted = [...wordEdges].sort((a, b) => a - b);
        if (dir === "out") {
          const next = sorted.find((t) => t > clip.end_sec);
          if (next !== undefined) newEnd = next;
        } else {
          const prev = [...sorted].reverse().find((t) => t < clip.end_sec && t > clip.start_sec);
          if (prev !== undefined) newEnd = prev;
        }
      }
      if (newStart === clip.start_sec && newEnd === clip.end_sec) {
        pushToast("err", "Already at a word boundary");
        return;
      }
      runMutation("Adjust", async () => {
        await api.adjust(clip.clip_id, newStart, newEnd);
      });
    },
    [singleSelected, data, flatWords, pushToast, runMutation]
  );

  const requestDelete = useCallback(() => {
    if (!singleSelected) return;
    setDeleteTarget(singleSelected.clip_id);
  }, [singleSelected]);

  const confirmDelete = useCallback(() => {
    if (!deleteTarget) return;
    const id = deleteTarget;
    setDeleteTarget(null);
    runMutation("Delete", async () => {
      await api.remove(id);
      setSelectedClips(new Set());
    });
  }, [deleteTarget, runMutation]);

  // ─── Keyboard shortcuts ──────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't fire when typing in any input field.
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      // m = merge (≥2 selected on same source)
      if (e.key === "m" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (selectedClips.size >= 2) {
          e.preventDefault();
          doMerge();
          return;
        }
      }
      // [ ] , .  with one selected → adjust boundaries
      if (singleSelected) {
        if (e.key === "[") {
          e.preventDefault();
          nudge("start", "out");
          return;
        }
        if (e.key === "]") {
          e.preventDefault();
          nudge("start", "in");
          return;
        }
        if (e.key === ",") {
          e.preventDefault();
          nudge("end", "in");
          return;
        }
        if (e.key === ".") {
          e.preventDefault();
          nudge("end", "out");
          return;
        }
      }
      // Cmd/Ctrl + Backspace → delete with confirm
      if (e.key === "Backspace" && (e.metaKey || e.ctrlKey)) {
        if (singleSelected) {
          e.preventDefault();
          requestDelete();
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selectedClips, singleSelected, doMerge, nudge, requestDelete]);

  if (!sourceId) {
    return (
      <div className="text-neutral-500 text-sm p-8 text-center">
        Pick a source from the left to see its raw transcript.
      </div>
    );
  }

  // Footage-only source still supports create-from-numeric-range.
  if (error && error.includes("footage-only")) {
    return (
      <div className="flex flex-col h-full">
        <div className="border-b border-neutral-800 pb-3 mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold font-mono">(no transcript)</h2>
          <button
            onClick={() => setShowCreateDialog(true)}
            className="text-xs px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800"
          >
            Create clip by range
          </button>
        </div>
        <div className="text-amber-300/80 text-sm p-6">{error}</div>
        {showCreateDialog && (
          <CreateClipDialog
            sourceId={sourceId}
            duration={null}
            onClose={() => setShowCreateDialog(false)}
            onCreated={() => {
              setShowCreateDialog(false);
              pushToast("ok", "Create clip");
              onAfterMutation();
            }}
          />
        )}
      </div>
    );
  }

  if (error) {
    return <div className="text-amber-300 text-sm p-6">{error}</div>;
  }
  if (!data) {
    return <div className="text-neutral-500 text-sm p-6">Loading…</div>;
  }

  const tintFor = (clipId: string | null): string => {
    if (!clipId) return "";
    const idx = data.clips.findIndex((c) => c.clip_id === clipId);
    return CLIP_TINTS[idx % CLIP_TINTS.length];
  };

  return (
    <div className="flex flex-col h-full">
      <div className="border-b border-neutral-800 pb-3 mb-3">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold font-mono truncate">{data.filename}</h2>
          <button
            onClick={() => setShowCreateDialog(true)}
            className="shrink-0 text-xs px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800"
          >
            + Create clip by range
          </button>
        </div>
        <div className="text-xs text-neutral-400 mt-1">
          {formatDuration(data.duration_sec)} · {data.clips.length} clips ·{" "}
          {flatWords.length} words
        </div>
      </div>

      {/* Action bar */}
      <div className="mb-3 min-h-[2.5rem]">
        {selectedClips.size === 0 && (
          <div className="text-xs text-neutral-500">
            Click a word in a clip to select it. Hold ⌘ to multi-select clips.
            Press <kbd>m</kbd> to merge. <kbd>[</kbd>/<kbd>]</kbd> /{" "}
            <kbd>,</kbd>/<kbd>.</kbd> nudge boundaries.{" "}
            <kbd>⌘ ⌫</kbd> to delete.
          </div>
        )}
        {singleSelected && (
          <div className="flex flex-wrap gap-2 items-center text-xs">
            <span className="text-neutral-400">
              <code>{singleSelected.clip_id}</code> ·{" "}
              {formatTimestamp(singleSelected.start_sec)} →{" "}
              {formatTimestamp(singleSelected.end_sec)}
            </span>
            <button
              onClick={doSplit}
              className="px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800"
              title="Split at midpoint"
            >
              Split @midpoint
            </button>
            <span className="text-neutral-500">|</span>
            <button onClick={() => nudge("start", "out")} className="px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800" title="[ — extend start outward">[</button>
            <button onClick={() => nudge("start", "in")} className="px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800" title="] — shrink start inward">]</button>
            <button onClick={() => nudge("end", "in")} className="px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800" title=", — shrink end inward">,</button>
            <button onClick={() => nudge("end", "out")} className="px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800" title=". — extend end outward">.</button>
            <span className="text-neutral-500">|</span>
            <button
              onClick={requestDelete}
              className="px-2 py-1 rounded-md border border-red-800 text-red-300 hover:bg-red-900/30"
            >
              Delete
            </button>
          </div>
        )}
        {selectedClips.size >= 2 && (
          <div className="flex flex-wrap gap-2 items-center text-xs">
            <span className="text-neutral-400">
              {selectedClips.size} clips selected
            </span>
            <button
              onClick={doMerge}
              className="px-3 py-1 rounded-md bg-white text-neutral-950 font-medium hover:bg-neutral-200"
            >
              Merge {selectedClips.size} clips
            </button>
            <button
              onClick={() => setSelectedClips(new Set())}
              className="px-2 py-1 rounded-md border border-neutral-700 hover:bg-neutral-800"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto leading-7 text-sm whitespace-pre-wrap"
      >
        {flatWords.map(({ idx, word, clipId }) => {
          const isSelected = clipId !== null && selectedClips.has(clipId);
          const tint = tintFor(clipId);
          return (
            <span
              key={idx}
              data-word-index={idx}
              data-start={word.start}
              data-end={word.end}
              data-clip-id={clipId ?? ""}
              className={`${tint} ${
                isSelected ? "ring-1 ring-amber-400/80 rounded-sm" : ""
              } ${clipId ? "cursor-pointer" : ""}`}
              onClick={(e) => {
                if (!clipId) return;
                setSelectedClips((prev) => {
                  const next = new Set(prev);
                  if (e.metaKey || e.ctrlKey) {
                    // Toggle this clip in/out of selection.
                    if (next.has(clipId)) next.delete(clipId);
                    else next.add(clipId);
                  } else {
                    // Replace selection with just this clip.
                    next.clear();
                    next.add(clipId);
                  }
                  return next;
                });
              }}
              title={
                clipId
                  ? `clip ${clipId} (${formatTimestamp(word.start)})`
                  : formatTimestamp(word.start)
              }
            >
              {word.word}
            </span>
          );
        })}
      </div>

      {deleteTarget && (
        <ConfirmDialog
          title="Delete clip?"
          body={`This removes ${deleteTarget} from the library. You can restore from the .clipfarm/snapshots/ folder if you regret this.`}
          confirmLabel="Delete"
          destructive
          onConfirm={confirmDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {showCreateDialog && (
        <CreateClipDialog
          sourceId={sourceId}
          duration={data.duration_sec}
          onClose={() => setShowCreateDialog(false)}
          onCreated={() => {
            setShowCreateDialog(false);
            pushToast("ok", "Create clip");
            onAfterMutation();
          }}
        />
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Search panel
// ───────────────────────────────────────────────────────────────────────────

function SearchPanel({
  onHitClick,
}: {
  onHitClick: (sourceId: string, wordIndex: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      // Minimum query length defends against ?q=a returning the whole library.
      setResponse(null);
      return;
    }
    const handle = setTimeout(async () => {
      setBusy(true);
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        if (!r.ok) {
          setResponse(null);
          return;
        }
        setResponse(await r.json());
      } finally {
        setBusy(false);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [query]);

  return (
    <div className="space-y-2">
      <div className="flex gap-2 items-center">
        <input
          type="search"
          className="flex-1 rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm"
          placeholder='Search every transcript (≥ 2 chars; "custody", "bitcoin", …)'
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {busy && <span className="text-xs text-neutral-500">searching…</span>}
        {response && !busy && (
          <span className="text-xs text-neutral-500">
            {response.total} hit{response.total === 1 ? "" : "s"}
            {response.truncated && ` (showing ${response.hits.length})`}
          </span>
        )}
      </div>
      {response && response.hits.length > 0 && (
        <div className="max-h-80 overflow-y-auto rounded-md border border-neutral-800 divide-y divide-neutral-800">
          {response.hits.map((h, i) => (
            <button
              key={`${h.source_id}-${h.word_index}-${i}`}
              onClick={() => onHitClick(h.source_id, h.word_index)}
              className="w-full text-left p-2 hover:bg-neutral-900 text-xs"
            >
              <div className="flex justify-between gap-2 items-baseline">
                <code className="text-neutral-300">{h.filename}</code>
                <span className="text-neutral-500">{formatTimestamp(h.timestamp_sec)}</span>
              </div>
              <div className="mt-1 text-neutral-400 truncate">
                <span>{h.context_before}</span>
                <span className="text-amber-300 font-medium">{h.match}</span>
                <span>{h.context_after}</span>
              </div>
            </button>
          ))}
        </div>
      )}
      {response && response.hits.length === 0 && !busy && (
        <div className="text-neutral-500 text-xs">No hits for “{response.query}”.</div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Toasts
// ───────────────────────────────────────────────────────────────────────────

type Toast = { id: number; kind: "ok" | "err"; text: string };

function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="fixed bottom-4 right-4 z-40 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`px-3 py-2 rounded-md text-sm shadow ${
            t.kind === "ok"
              ? "bg-emerald-700 text-white"
              : "bg-red-700 text-white"
          }`}
        >
          {t.text}
        </div>
      ))}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Library page
// ───────────────────────────────────────────────────────────────────────────

export default function Library() {
  const [state, setState] = useState<AppState | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();

  const refreshState = useCallback(async () => {
    const r = await fetch("/api/state");
    if (r.ok) setState(await r.json());
  }, []);

  useEffect(() => {
    refreshState();
  }, [refreshState]);

  // Honor `?source=<id>&word=<idx>` deep-link on mount / nav-from-other-page.
  // Used by the Project → Take Grid "Open in Library" affordance: the take
  // grid response carries `first_word_index` per card, so we don't have to
  // walk the transcript here.
  useEffect(() => {
    const source = searchParams.get("source");
    if (!source) return;
    setSelectedSource(source);
    const wordRaw = searchParams.get("word");
    const word = wordRaw == null ? NaN : Number(wordRaw);
    if (Number.isFinite(word) && word >= 0) {
      setFocusRequest({ wordIndex: word, nonce: Date.now() });
    } else {
      setFocusRequest(null);
    }
    // Clear the params once consumed so a later same-page nav (e.g.
    // clicking a different source in the sidebar) isn't undone by a
    // stale ?source=.
    setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pushToast = useCallback((kind: "ok" | "err", text: string) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, kind, text }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, kind === "ok" ? 1800 : 4500);
  }, []);

  const onAfterMutation = useCallback(() => {
    refreshState();
    setRefreshNonce((n) => n + 1);
  }, [refreshState]);

  const sources = state ? Object.entries(state.sources) : [];
  const clipsBySource = useMemo(() => {
    const out: Record<string, number> = {};
    if (state) {
      for (const c of Object.values(state.clips)) {
        out[c.source_id] = (out[c.source_id] ?? 0) + 1;
      }
    }
    return out;
  }, [state]);

  const onHitClick = useCallback((sourceId: string, wordIndex: number) => {
    setSelectedSource(sourceId);
    setFocusRequest({ wordIndex, nonce: Date.now() });
  }, []);

  return (
    <section className="space-y-4 h-[calc(100vh-7rem)] flex flex-col">
      <div>
        <h1 className="text-2xl font-semibold">Library</h1>
        <p className="text-neutral-400 text-sm mt-1">
          Pick a source; clip boundaries appear inline. Select clips to
          split / merge / adjust / delete them. Boundary correction writes a
          snapshot before every change — restore from <code>.clipfarm/snapshots/</code>{" "}
          if needed.
        </p>
      </div>

      <SearchPanel onHitClick={onHitClick} />

      <div className="flex-1 grid grid-cols-[280px_1fr] gap-4 min-h-0">
        <div className="space-y-3 overflow-y-auto pr-2">
          <IngestPanel onAfterIngest={refreshState} />
          <div className="rounded-md border border-neutral-800 bg-neutral-900/50">
            <div className="px-3 py-2 text-sm font-medium border-b border-neutral-800">
              Sources{" "}
              <span className="text-neutral-500 text-xs font-normal">
                ({sources.length})
              </span>
            </div>
            {sources.length === 0 ? (
              <div className="px-3 py-4 text-xs text-neutral-500">
                None yet. Ingest a folder above.
              </div>
            ) : (
              <ul className="divide-y divide-neutral-800">
                {sources.map(([sid, src]) => {
                  const selected = sid === selectedSource;
                  return (
                    <li key={sid}>
                      <button
                        onClick={() => {
                          setSelectedSource(sid);
                          setFocusRequest(null);
                        }}
                        className={`w-full text-left px-3 py-2 text-xs hover:bg-neutral-900 ${
                          selected ? "bg-neutral-800" : ""
                        } ${src.unavailable ? "opacity-50" : ""}`}
                      >
                        <div className="font-mono truncate">
                          {src.filename}
                          {src.unavailable && (
                            <span className="ml-2 text-amber-400 text-[10px]">
                              unavailable
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 text-neutral-500 text-[10px] flex justify-between">
                          <span>
                            {formatDuration(src.duration_sec)} ·{" "}
                            {clipsBySource[sid] ?? 0} clips
                          </span>
                          <span>{src.transcript_path ? "" : "footage-only"}</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        <div className="rounded-md border border-neutral-800 bg-neutral-900/30 p-4 min-h-0">
          <TranscriptView
            sourceId={selectedSource}
            focus={focusRequest}
            refreshNonce={refreshNonce}
            pushToast={pushToast}
            onAfterMutation={onAfterMutation}
          />
        </div>
      </div>

      <Toasts toasts={toasts} />
    </section>
  );
}
