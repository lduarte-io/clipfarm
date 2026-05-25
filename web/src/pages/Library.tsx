import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// Phase 3 layout: left rail for ingest controls + source list, main panel
// for the selected source's transcript with clip boundaries marked inline,
// search bar at the top with debounced live results.

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

type WhisperWord = { start: number; end: number; word: string; probability?: number };
type WhisperSegment = { id?: number; start: number; end: number; text?: string; words: WhisperWord[] };
type ClipRange = { clip_id: string; start_sec: number; end_sec: number };
type TranscriptView = {
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

// Alternating tints so adjacent clip ranges read as distinct without being
// loud. Stronger highlight is applied to the selected clip via `data-selected`.
const CLIP_TINTS = ["bg-neutral-800/60", "bg-neutral-700/60"];

// ───────────────────────────────────────────────────────────────────────────
// Ingest panel — same affordance as Phase 2, now collapsible in the left rail.
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
// Transcript view — words rendered inline, clip boundaries tinted.
// ───────────────────────────────────────────────────────────────────────────

type FocusRequest = { wordIndex: number; nonce: number };

function TranscriptView({
  sourceId,
  focus,
  onClipSelect,
}: {
  sourceId: string | null;
  focus: FocusRequest | null;
  onClipSelect: (clipId: string | null) => void;
}) {
  const [data, setData] = useState<TranscriptView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedClip, setSelectedClip] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    setSelectedClip(null);
    if (!sourceId) return;
    let cancelled = false;
    (async () => {
      const r = await fetch(`/api/sources/${encodeURIComponent(sourceId)}/transcript`);
      if (cancelled) return;
      if (r.status === 422) {
        setError("This source has no transcript yet (footage-only).");
        return;
      }
      if (!r.ok) {
        setError(`Failed to load transcript: ${r.status} ${r.statusText}`);
        return;
      }
      const body: TranscriptView = await r.json();
      setData(body);
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  // Flatten words once with word_index + the clip range they belong to (if
  // any). The Phase 4 split/merge popovers will reuse this index.
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
  }, [data]);

  // Handle external focus (search-result jump): scroll to the word + flash
  // its containing clip.
  useEffect(() => {
    if (!focus || !containerRef.current) return;
    const el = containerRef.current.querySelector(
      `[data-word-index="${focus.wordIndex}"]`
    ) as HTMLElement | null;
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const clipId = el.getAttribute("data-clip-id");
    if (clipId) {
      setSelectedClip(clipId);
      onClipSelect(clipId);
    }
    el.classList.add("ring-2", "ring-amber-400");
    const tid = setTimeout(() => {
      el.classList.remove("ring-2", "ring-amber-400");
    }, 1600);
    return () => clearTimeout(tid);
  }, [focus, onClipSelect]);

  if (!sourceId) {
    return (
      <div className="text-neutral-500 text-sm p-8 text-center">
        Pick a source from the left to see its raw transcript.
      </div>
    );
  }
  if (error) {
    return <div className="text-amber-300 text-sm p-6">{error}</div>;
  }
  if (!data) {
    return <div className="text-neutral-500 text-sm p-6">Loading…</div>;
  }

  // Build a per-clip tint index so adjacent clips alternate colors.
  const tintFor = (clipId: string | null): string => {
    if (!clipId) return "";
    const idx = data.clips.findIndex((c) => c.clip_id === clipId);
    return CLIP_TINTS[idx % CLIP_TINTS.length];
  };

  return (
    <div className="flex flex-col h-full">
      <div className="border-b border-neutral-800 pb-3 mb-3">
        <h2 className="text-lg font-semibold font-mono">{data.filename}</h2>
        <div className="text-xs text-neutral-400 mt-1">
          {formatDuration(data.duration_sec)} · {data.clips.length} clips ·{" "}
          {flatWords.length} words
        </div>
      </div>
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto leading-7 text-sm whitespace-pre-wrap"
      >
        {flatWords.map(({ idx, word, clipId }) => {
          const isSelected = selectedClip && clipId === selectedClip;
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
              onClick={() => {
                if (!clipId) return;
                const next = clipId === selectedClip ? null : clipId;
                setSelectedClip(next);
                onClipSelect(next);
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
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Search results — debounced live results inline below the search bar.
// ───────────────────────────────────────────────────────────────────────────

function SearchPanel({
  onHitClick,
}: {
  onHitClick: (sourceId: string, wordIndex: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);

  // Debounce the actual fetch.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
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
          placeholder='Search every transcript ("custody", "bitcoin", ...)'
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
// Library page — composes everything together.
// ───────────────────────────────────────────────────────────────────────────

export default function Library() {
  const [state, setState] = useState<AppState | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);

  const refreshState = useCallback(async () => {
    const r = await fetch("/api/state");
    if (r.ok) setState(await r.json());
  }, []);

  useEffect(() => {
    refreshState();
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
    // Nonce forces the effect in TranscriptView to re-fire even if the
    // user clicks the same hit twice.
    setFocusRequest({ wordIndex, nonce: Date.now() });
  }, []);

  return (
    <section className="space-y-4 h-[calc(100vh-7rem)] flex flex-col">
      <div>
        <h1 className="text-2xl font-semibold">Library</h1>
        <p className="text-neutral-400 text-sm mt-1">
          Pick a source on the left for its raw transcript; clip boundaries
          appear inline. Search to scan every transcript at once.
        </p>
      </div>

      <SearchPanel onHitClick={onHitClick} />

      <div className="flex-1 grid grid-cols-[280px_1fr] gap-4 min-h-0">
        {/* Left rail */}
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

        {/* Main panel */}
        <div className="rounded-md border border-neutral-800 bg-neutral-900/30 p-4 min-h-0">
          <TranscriptView
            sourceId={selectedSource}
            focus={focusRequest}
            onClipSelect={() => {}}
          />
        </div>
      </div>
    </section>
  );
}
