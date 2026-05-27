import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SidePanel } from "../components/SidePanel";
import { usePlayback } from "../playback/context";
import {
  useActiveAttempt,
  useActiveAttemptValidation,
} from "../playback/active-attempt";

// Phase 7b — Script TOC view. Same data as the Phase 7 Take Grid
// (`/api/projects/{id}/take-grid`), different layout: vertical outline
// where each script line is a collapsible <details> showing its takes
// inside. Lillian's "browse the script as the script" view; the Take
// Grid is for cross-line scanning, the TOC is for working one line
// top-to-bottom.
//
// No backend changes — both pages call the same endpoint. The Card +
// SidePanel components are deliberately duplicated from Project.tsx;
// two implementations isn't an abstraction trigger yet (project rule:
// wait for the third use).

type Category =
  | "on-script"
  | "related-but-different"
  | "standalone-idea"
  | "off-topic"
  | "fragment";

type TakeCard = {
  clip_id: string;
  source_id: string;
  filename: string;
  start_sec: number;
  end_sec: number;
  transcript_text: string;
  category: Category;
  confidence: number;
  project_tag_id: string | null;
  stale: boolean;
  first_word_index: number | null;
};

type LineRow = {
  tag_id: string;
  name: string;
  order_idx: number;
  cards: TakeCard[];
};

type BucketView = { cards: TakeCard[] };

type TakeGridSummary = {
  untagged_clips: number;
  stale_clips: number;
  total_tagged: number;
};

type TakeGridView = {
  project_id: string;
  name: string;
  lines: LineRow[];
  buckets: Record<string, BucketView>;
  summary: TakeGridSummary;
};

type ProjectListItem = { project_id: string; name: string };

type AppState = {
  projects: Record<string, { name: string }>;
  // Phase 10a — attempts tracked for the + add-clip handler + the
  // active-attempt validation hook.
  attempts: Record<string, {
    project_id: string;
    name: string;
    clips: Array<{
      clip_id: string;
      trim_start_offset: number;
      trim_end_offset: number;
      internal_pause_max_sec: number | null;
      notes: string;
    }>;
  }>;
};

// Phase 10a — same helpers as Project.tsx for the + add-clip flow.
// Duplicated rather than extracted; if a third take page needs them
// they'd land in a shared module (project rule: three-uses-trigger).
async function addClipToActiveAttempt(
  activeAttemptId: string,
  card: { clip_id: string },
  attemptsState: AppState["attempts"],
): Promise<number | null> {
  const att = attemptsState[activeAttemptId];
  if (!att) return null;
  const nextClips = [...att.clips, {
    clip_id: card.clip_id,
    trim_start_offset: 0.0,
    trim_end_offset: 0.0,
    internal_pause_max_sec: null,
    notes: "",
  }];
  const r = await fetch(`/api/attempts/${activeAttemptId}/clips`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clips: nextClips }),
  });
  if (!r.ok) return null;
  const body = await r.json();
  return body.attempt.clips.length as number;
}

async function createAttemptWithClip(
  projectId: string,
  card: { clip_id: string },
): Promise<string | null> {
  const r = await fetch(`/api/projects/${projectId}/attempts`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: "new attempt",
      clips: [{
        clip_id: card.clip_id,
        trim_start_offset: 0.0,
        trim_end_offset: 0.0,
        internal_pause_max_sec: null,
        notes: "",
      }],
    }),
  });
  if (!r.ok) return null;
  const body = await r.json();
  return body.attempt_id as string;
}

const BUCKET_ORDER: Category[] = [
  "related-but-different",
  "standalone-idea",
  "off-topic",
  "fragment",
];

const BUCKET_LABELS: Record<string, string> = {
  "related-but-different": "Related-but-different",
  "standalone-idea": "Standalone ideas",
  "off-topic": "Off-topic",
  "fragment": "Fragments / restarts",
};

const CATEGORY_BADGE: Record<Category, string> = {
  "on-script": "bg-emerald-900/40 text-emerald-300 border-emerald-800",
  "related-but-different": "bg-sky-900/40 text-sky-300 border-sky-800",
  "standalone-idea": "bg-violet-900/40 text-violet-300 border-violet-800",
  "off-topic": "bg-neutral-800 text-neutral-400 border-neutral-700",
  "fragment": "bg-neutral-900 text-neutral-500 border-neutral-800",
};

function formatTimestamp(sec: number): string {
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : text.slice(0, max - 1).trimEnd() + "…";
}

// ───────────────────────────────────────────────────────────────────────────
// Card — TOC layout: full-width vertical stack, ~3-line snippet visible
// without horizontal scrolling. Compare to Phase 7's 220px-wide horizontal
// strip card; same data, different shape.
// ───────────────────────────────────────────────────────────────────────────

function TocCard({
  card,
  selected,
  onSelect,
  onAdd,
  addTooltip,
}: {
  card: TakeCard;
  selected: boolean;
  onSelect: () => void;
  onAdd?: () => void;
  addTooltip?: string;
}) {
  const tint = selected ? "ring-1 ring-white/60" : "ring-1 ring-neutral-800";
  return (
    <div className={`relative rounded-md bg-neutral-900 hover:bg-neutral-800/80 ${tint} transition-colors`}>
      <button
        onClick={onSelect}
        className="w-full text-left p-3"
      >
        <div className="flex items-center gap-2 text-[10px] mb-1.5">
          <span
            className={`px-1.5 py-0.5 rounded border ${CATEGORY_BADGE[card.category]}`}
          >
            {card.category}
          </span>
          <span className="text-neutral-500">
            {(card.confidence * 100).toFixed(0)}%
          </span>
          <span className="text-neutral-600 font-mono">·</span>
          <span className="text-neutral-500 font-mono truncate">
            {card.filename}
          </span>
          <span className="text-neutral-500 font-mono">
            {formatTimestamp(card.start_sec)}–{formatTimestamp(card.end_sec)}
          </span>
          {card.stale && (
            <span
              className="ml-auto h-2 w-2 rounded-full bg-amber-400"
              title="Stale — brief changed after this tag was written. Re-tag to refresh."
            />
          )}
        </div>
        <div className="text-sm text-neutral-200 leading-snug pr-5">
          {truncate(card.transcript_text || "(no transcript)", 280)}
        </div>
      </button>
      {onAdd && (
        <button
          onClick={(e) => { e.stopPropagation(); onAdd(); }}
          className="absolute top-1.5 right-1.5 text-neutral-400 hover:text-white hover:bg-neutral-700 rounded w-5 h-5 flex items-center justify-center text-sm leading-none"
          title={addTooltip ?? "Add to attempt"}
          aria-label="Add to attempt"
        >
          +
        </button>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Side panel — duplicate of Project.tsx's SidePanel. Phase 9 will swap
// the body for a live <video> preview; that change ripples to both
// pages then (worth extracting at that point — three uses minus one).
// ───────────────────────────────────────────────────────────────────────────

function CardSidePanel({
  card,
  onClose,
}: {
  card: TakeCard | null;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const openInLibrary = () => {
    if (!card) return;
    const params = new URLSearchParams({ source: card.source_id });
    if (card.first_word_index != null) {
      params.set("word", String(card.first_word_index));
    }
    navigate(`/library?${params.toString()}`);
  };
  return (
    <SidePanel
      open={card != null}
      emptyMessage="Pick a take to see its full transcript."
      onClose={onClose}
      header={
        card && (
          <>
            <div className="text-xs font-mono text-neutral-400 truncate">
              {card.filename}
            </div>
            <div className="text-xs text-neutral-500 font-mono">
              {formatTimestamp(card.start_sec)} →{" "}
              {formatTimestamp(card.end_sec)}
            </div>
          </>
        )
      }
    >
      {card && (
        <>
          <div className="flex items-center gap-1.5 text-[10px]">
            <span
              className={`px-1.5 py-0.5 rounded border ${CATEGORY_BADGE[card.category]}`}
            >
              {card.category}
            </span>
            <span className="text-neutral-500">
              confidence {(card.confidence * 100).toFixed(0)}%
            </span>
            {card.stale && (
              <span
                className="text-amber-400"
                title="Re-tag from the Brief page to refresh."
              >
                stale
              </span>
            )}
          </div>
          <div className="text-sm text-neutral-100 leading-relaxed whitespace-pre-wrap">
            {card.transcript_text || "(no transcript)"}
          </div>
          <button
            onClick={openInLibrary}
            className="w-full rounded-md bg-white text-neutral-950 font-medium px-3 py-1.5 text-xs hover:bg-neutral-200"
          >
            Open in Library
          </button>
        </>
      )}
    </SidePanel>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Line outline node — a script line as a collapsible <details>
// ───────────────────────────────────────────────────────────────────────────

function LineOutline({
  row,
  index,
  selectedClipId,
  onSelect,
  onAddClip,
  addTooltip,
}: {
  row: LineRow;
  index: number;
  selectedClipId: string | null;
  onSelect: (card: TakeCard) => void;
  onAddClip?: (card: TakeCard) => void;
  addTooltip?: string;
}) {
  const hasTakes = row.cards.length > 0;
  // Empty rows are visually de-emphasized so the user can see the gap
  // ("line 4 has nothing yet") but their eye doesn't get pulled there.
  const headerCls = hasTakes
    ? "text-neutral-100"
    : "text-neutral-500 italic";
  return (
    <details className="rounded-md border border-neutral-800 bg-neutral-950/40 group">
      <summary className="cursor-pointer px-3 py-2.5 select-none flex items-baseline gap-2">
        <span className="text-xs font-mono text-neutral-600 tabular-nums w-7 shrink-0">
          {String(index + 1).padStart(2, "0")}.
        </span>
        <span className={`flex-1 min-w-0 text-sm leading-snug ${headerCls}`}>
          {row.name}
        </span>
        <span className="text-[10px] font-mono text-neutral-600 shrink-0">
          {row.tag_id}
        </span>
        <span
          className={`text-xs shrink-0 ${
            hasTakes ? "text-neutral-300" : "text-neutral-600"
          }`}
        >
          {row.cards.length} {row.cards.length === 1 ? "take" : "takes"}
        </span>
      </summary>
      <div className="px-3 pb-3 pt-1 space-y-2 border-t border-neutral-800/60">
        {hasTakes ? (
          row.cards.map((c) => (
            <TocCard
              key={`${c.clip_id}-${c.category}-${c.project_tag_id ?? "none"}`}
              card={c}
              selected={selectedClipId === c.clip_id}
              onSelect={() => onSelect(c)}
              onAdd={onAddClip ? () => onAddClip(c) : undefined}
              addTooltip={addTooltip}
            />
          ))
        ) : (
          <div className="text-xs text-neutral-600 italic px-1 py-2">
            No matched takes yet. Run "Tag clips" from the Brief page after
            adding more footage.
          </div>
        )}
      </div>
    </details>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Bucket section — flush at the bottom of the TOC, same shape as Phase 7
// ───────────────────────────────────────────────────────────────────────────

function BucketOutline({
  category,
  cards,
  selectedClipId,
  onSelect,
  onAddClip,
  addTooltip,
}: {
  category: Category;
  cards: TakeCard[];
  selectedClipId: string | null;
  onSelect: (card: TakeCard) => void;
  onAddClip?: (card: TakeCard) => void;
  addTooltip?: string;
}) {
  return (
    <details className="rounded-md border border-neutral-800 bg-neutral-950/40">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium select-none flex items-center gap-2">
        <span>{BUCKET_LABELS[category]}</span>
        <span className="text-xs text-neutral-500 font-normal">
          {cards.length}
        </span>
      </summary>
      <div className="p-3 border-t border-neutral-800 space-y-2">
        {cards.length === 0 ? (
          <div className="text-xs text-neutral-600 italic">Empty.</div>
        ) : (
          cards.map((c) => (
            <TocCard
              key={`${c.clip_id}-${c.category}`}
              card={c}
              selected={selectedClipId === c.clip_id}
              onSelect={() => onSelect(c)}
              onAdd={onAddClip ? () => onAddClip(c) : undefined}
              addTooltip={addTooltip}
            />
          ))
        )}
      </div>
    </details>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Page
// ───────────────────────────────────────────────────────────────────────────

export default function ScriptTOC() {
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [grid, setGrid] = useState<TakeGridView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedCard, setSelectedCard] = useState<TakeCard | null>(null);
  // Null = not loaded yet (initial state); {} = loaded with zero attempts.
  // Distinguishing the two prevents useActiveAttemptValidation from
  // clearing the active attempt during the fetch-in-flight window.
  const [appAttempts, setAppAttempts] = useState<AppState["attempts"] | null>(null);
  const { playClip } = usePlayback();
  const { activeAttemptId, setActiveAttemptId } = useActiveAttempt();
  useActiveAttemptValidation(appAttempts, projectId);

  // Phase 9 — clicking a TakeCard both opens the side panel AND starts
  // preview playback. Same pattern as Project.tsx.
  const onCardSelect = useCallback(
    (card: TakeCard) => {
      setSelectedCard(card);
      playClip({
        clip_id: card.clip_id,
        source_id: card.source_id,
        source_filename: card.filename,
        start_sec: card.start_sec,
        end_sec: card.end_sec,
      });
    },
    [playClip],
  );

  useEffect(() => {
    let cancelled = false;
    fetch("/api/state")
      .then((r) => r.json() as Promise<AppState>)
      .then((s) => {
        if (cancelled) return;
        const list = Object.entries(s.projects ?? {}).map(([pid, p]) => ({
          project_id: pid,
          name: p.name,
        }));
        setProjects(list);
        setAppAttempts(s.attempts ?? {});
        if (list.length > 0 && projectId == null) {
          setProjectId(list[0].project_id);
        }
      })
      .catch((e) => !cancelled && setLoadError(String(e)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Phase 10a — re-fetch state when an add-clip writes (so the active
  // attempt's clip count reflects the change immediately).
  const refreshAttempts = useCallback(async () => {
    const r = await fetch("/api/state");
    if (!r.ok) return;
    const s: AppState = await r.json();
    setAppAttempts(s.attempts ?? {});
  }, []);

  const onAddClipToActive = useCallback(
    async (card: TakeCard) => {
      if (!projectId) return;
      if (activeAttemptId && appAttempts && appAttempts[activeAttemptId]) {
        await addClipToActiveAttempt(activeAttemptId, card, appAttempts);
      } else {
        const newId = await createAttemptWithClip(projectId, card);
        if (newId) setActiveAttemptId(newId);
      }
      refreshAttempts();
    },
    [activeAttemptId, appAttempts, projectId, refreshAttempts, setActiveAttemptId],
  );
  const addTooltip = activeAttemptId
    ? `Add to attempt #${activeAttemptId}`
    : "Start a new attempt with this clip";

  const loadGrid = useCallback(async (pid: string) => {
    setLoadError(null);
    setSelectedCard(null);
    try {
      const r = await fetch(
        `/api/projects/${encodeURIComponent(pid)}/take-grid`
      );
      if (!r.ok) {
        const text = await r.text();
        throw new Error(`${r.status}: ${text}`);
      }
      const body: TakeGridView = await r.json();
      setGrid(body);
    } catch (e) {
      setLoadError(String(e));
      setGrid(null);
    }
  }, []);

  useEffect(() => {
    if (projectId) loadGrid(projectId);
  }, [projectId, loadGrid]);

  const totalCardsInGrid = useMemo(() => {
    if (!grid) return 0;
    let n = 0;
    for (const row of grid.lines) n += row.cards.length;
    for (const b of Object.values(grid.buckets)) n += b.cards.length;
    return n;
  }, [grid]);

  // ---- Empty / loading / error states ----

  if (projects == null) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Script</h1>
        <p className="text-neutral-500 text-sm">Loading…</p>
      </section>
    );
  }

  if (projects.length === 0) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Script</h1>
        <p className="text-neutral-400 text-sm">
          No projects yet.{" "}
          <Link to="/brief" className="underline hover:text-white">
            Write a brief
          </Link>{" "}
          to create one — the script lines you put there become the rows
          of this outline.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-2xl font-semibold">Script</h1>
        {projects.length > 1 ? (
          <select
            value={projectId ?? ""}
            onChange={(e) => setProjectId(e.target.value)}
            className="rounded-md bg-neutral-900 border border-neutral-700 px-2 py-1 text-sm"
          >
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-sm text-neutral-300">
            {projects[0].name}
          </span>
        )}
        <p className="text-xs text-neutral-500 ml-auto max-w-md">
          Read the script top-to-bottom. Expand a line to see its takes.
          Use the{" "}
          <Link to="/project" className="underline hover:text-white">
            Project
          </Link>{" "}
          view to scan across lines instead.
        </p>
      </div>

      {loadError && (
        <div className="rounded-md border border-red-900 bg-red-950/40 p-3 text-xs text-red-300">
          Failed to load script TOC: {loadError}
        </div>
      )}

      {grid && totalCardsInGrid === 0 && (
        <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-4 text-sm text-neutral-300">
          <div className="font-medium mb-1">No tags yet for this project.</div>
          <div className="text-neutral-500">
            Head to the{" "}
            <Link to="/brief" className="underline hover:text-white">
              Brief page
            </Link>{" "}
            and hit "Tag clips" to populate the outline.
          </div>
        </div>
      )}

      {grid && totalCardsInGrid > 0 && (
        <div className="grid grid-cols-[1fr_320px] gap-4 items-start">
          <div className="space-y-3 min-w-0">
            <div className="space-y-2">
              {grid.lines.map((row, i) => (
                <LineOutline
                  key={row.tag_id}
                  row={row}
                  index={i}
                  selectedClipId={selectedCard?.clip_id ?? null}
                  onSelect={onCardSelect}
                  onAddClip={onAddClipToActive}
                  addTooltip={addTooltip}
                />
              ))}
            </div>
            <div className="space-y-2 pt-2">
              <div className="text-xs uppercase tracking-wide text-neutral-500">
                Other categories
              </div>
              {BUCKET_ORDER.map((cat) => (
                <BucketOutline
                  key={cat}
                  category={cat}
                  cards={grid.buckets[cat]?.cards ?? []}
                  selectedClipId={selectedCard?.clip_id ?? null}
                  onSelect={onCardSelect}
                  onAddClip={onAddClipToActive}
                  addTooltip={addTooltip}
                />
              ))}
            </div>
          </div>
          <CardSidePanel
            card={selectedCard}
            onClose={() => setSelectedCard(null)}
          />
        </div>
      )}
    </section>
  );
}
