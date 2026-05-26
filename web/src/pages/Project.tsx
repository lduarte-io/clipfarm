import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  PremadeProgressPanel,
  useRunProgress,
} from "../components/RunProgress";
import { SidePanel } from "../components/SidePanel";
import { usePlayback } from "../playback/context";
import {
  useActiveAttempt,
  useActiveAttemptValidation,
} from "../playback/active-attempt";

// Phase 7 — Take Grid view. After Phase 6 tags clips into
// `clip_project_tags`, this page reads `GET /api/projects/{id}/take-grid`
// and lays out every script line as a row of "take cards" plus four
// collapsible buckets for the off-script categories.
//
// Side panel on the right holds the selected card's full transcript +
// the "Open in Library" affordance. Phase 9 will swap the side panel's
// content for a live `<video>` preview without changing this layout.

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

// Minimal Attempt shape for the Phase 8 summary panel — only the
// fields the panel renders. Full Attempt detail lives on the Attempts
// page; this page links over.
type AttemptSummary = {
  attempt_id: string;
  name: string;
  source: "ai-premade" | "hand-built" | "fork";
  premade_bucket: "best" | "diagnostic" | null;
  continuity_score: number | null;
  clip_count: number;
};

type AppState = {
  projects: Record<string, { name: string }>;
  attempts: Record<string, {
    project_id: string;
    name: string;
    source: "ai-premade" | "hand-built" | "fork";
    premade_bucket: "best" | "diagnostic" | null;
    continuity_score: number | null;
    clips: Array<unknown>;
  }>;
};

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

// Open the first two buckets by default — they're the high-signal
// "this is useful in a different way" pools. Off-topic + fragments
// stay collapsed because the user usually doesn't want to scroll past
// 30 fragments to get to the next idea bucket.
const BUCKET_DEFAULT_OPEN: Record<string, boolean> = {
  "related-but-different": true,
  "standalone-idea": true,
  "off-topic": false,
  "fragment": false,
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
// Card
// ───────────────────────────────────────────────────────────────────────────

function Card({
  card,
  selected,
  onSelect,
  onAdd,
  addTooltip,
}: {
  card: TakeCard;
  selected: boolean;
  onSelect: () => void;
  /** Phase 10a — clicking the corner "+" adds this clip to the
   *  active attempt (or creates a new attempt with just this clip
   *  if there's no active one). Optional so the same Card component
   *  works in bucket sections that don't surface the action. */
  onAdd?: () => void;
  addTooltip?: string;
}) {
  const tint = selected ? "ring-1 ring-white/60" : "ring-1 ring-neutral-800";
  return (
    <div className={`w-[220px] shrink-0 relative rounded-md bg-neutral-900 hover:bg-neutral-800/80 ${tint} transition-colors`}>
      <button
        onClick={onSelect}
        className="w-full text-left p-3 space-y-2"
      >
        <div className="flex items-center gap-1.5 text-[10px]">
          <span
            className={`px-1.5 py-0.5 rounded border ${CATEGORY_BADGE[card.category]}`}
          >
            {card.category}
          </span>
          <span className="text-neutral-500">
            {(card.confidence * 100).toFixed(0)}%
          </span>
          {card.stale && (
            <span
              className="ml-auto h-2 w-2 rounded-full bg-amber-400"
              title="Stale — brief changed after this tag was written. Re-tag to refresh."
            />
          )}
        </div>
        <div className="text-[10px] text-neutral-500 font-mono truncate">
          {card.filename}
        </div>
        <div className="text-[10px] text-neutral-500 font-mono">
          {formatTimestamp(card.start_sec)}–{formatTimestamp(card.end_sec)}
        </div>
        <div className="text-xs text-neutral-200 leading-snug line-clamp-3 pr-5">
          {truncate(card.transcript_text || "(no transcript)", 180)}
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
// Side panel — full transcript + Open-in-Library link
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
      emptyMessage="Pick a card to see its full transcript."
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
// Line row — horizontally scrolling card strip
// ───────────────────────────────────────────────────────────────────────────

function LineRowView({
  row,
  selectedClipId,
  onSelect,
  onAddClip,
  addTooltip,
}: {
  row: LineRow;
  selectedClipId: string | null;
  onSelect: (card: TakeCard) => void;
  onAddClip?: (card: TakeCard) => void;
  addTooltip?: string;
}) {
  return (
    <div className="rounded-md border border-neutral-800 bg-neutral-950/40 p-3 space-y-2">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-medium text-neutral-100 truncate">
          {row.name}
        </h3>
        <span className="text-[10px] font-mono text-neutral-600">
          {row.tag_id}
        </span>
        <span className="ml-auto text-xs text-neutral-500">
          {row.cards.length} {row.cards.length === 1 ? "take" : "takes"}
        </span>
      </div>
      {row.cards.length === 0 ? (
        <div className="text-xs text-neutral-600 italic px-1 py-2">
          No matched takes yet.
        </div>
      ) : (
        <div className="flex gap-2 overflow-x-auto pb-2">
          {row.cards.map((c) => (
            <Card
              key={`${c.clip_id}-${c.category}-${c.project_tag_id ?? "none"}`}
              card={c}
              selected={selectedClipId === c.clip_id}
              onSelect={() => onSelect(c)}
              onAdd={onAddClip ? () => onAddClip(c) : undefined}
              addTooltip={addTooltip}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Bucket — flat card list, collapsible
// ───────────────────────────────────────────────────────────────────────────

function BucketSection({
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
    <details
      className="rounded-md border border-neutral-800 bg-neutral-950/40"
      open={BUCKET_DEFAULT_OPEN[category] && cards.length > 0}
    >
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium select-none flex items-center gap-2">
        <span>{BUCKET_LABELS[category]}</span>
        <span className="text-xs text-neutral-500 font-normal">
          {cards.length}
        </span>
      </summary>
      <div className="p-3 border-t border-neutral-800">
        {cards.length === 0 ? (
          <div className="text-xs text-neutral-600 italic">Empty.</div>
        ) : (
          <div className="flex gap-2 flex-wrap">
            {cards.map((c) => (
              <Card
                key={`${c.clip_id}-${c.category}`}
                card={c}
                selected={selectedClipId === c.clip_id}
                onSelect={() => onSelect(c)}
                onAdd={onAddClip ? () => onAddClip(c) : undefined}
                addTooltip={addTooltip}
              />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Attempts summary panel (Phase 8) — best-plausible only; diagnostic
// stays on /attempts because it's exploration, not assembly.
// ───────────────────────────────────────────────────────────────────────────

function continuityBar(score: number | null) {
  if (score == null) return { cls: "bg-neutral-700", label: "—" };
  const pct = Math.round(score * 100);
  if (score >= 0.8) return { cls: "bg-emerald-500", label: `${pct}%` };
  if (score >= 0.4) return { cls: "bg-amber-500", label: `${pct}%` };
  return { cls: "bg-red-500", label: `${pct}%` };
}

function AttemptsSummaryPanel({
  bestAttempts,
  totalCount,
  onNavigate,
}: {
  bestAttempts: AttemptSummary[];
  totalCount: number;
  onNavigate: () => void;
}) {
  if (bestAttempts.length === 0) return null;
  return (
    <details
      open
      className="rounded-md border border-neutral-800 bg-neutral-950/40"
    >
      <summary className="cursor-pointer px-3 py-2 select-none flex items-center gap-2">
        <span className="text-sm font-medium">Premade attempts</span>
        <span className="text-xs text-neutral-500">
          {bestAttempts.length} best-plausible
        </span>
        <button
          onClick={(e) => {
            e.preventDefault();
            onNavigate();
          }}
          className="ml-auto text-xs text-neutral-400 hover:text-white underline"
        >
          See all attempts ({totalCount}) →
        </button>
      </summary>
      <div className="border-t border-neutral-800 divide-y divide-neutral-800">
        {bestAttempts.map((a) => {
          const tone = continuityBar(a.continuity_score);
          return (
            <button
              key={a.attempt_id}
              onClick={onNavigate}
              className="w-full text-left px-3 py-2 hover:bg-neutral-900 flex items-center gap-3"
            >
              <span className="text-[10px] font-mono text-neutral-600 w-8 shrink-0">
                #{a.attempt_id}
              </span>
              <span className="flex-1 text-sm text-neutral-200 truncate min-w-0">
                {a.name}
              </span>
              <span className="text-xs text-neutral-500 shrink-0">
                {a.clip_count} clips
              </span>
              <div className="w-24 h-2 rounded-full bg-neutral-800 overflow-hidden shrink-0">
                <div
                  className={`h-full ${tone.cls}`}
                  style={{ width: `${(a.continuity_score ?? 0) * 100}%` }}
                />
              </div>
              <span className="text-[10px] font-mono text-neutral-400 w-10 text-right shrink-0">
                {tone.label}
              </span>
            </button>
          );
        })}
      </div>
    </details>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Page
// ───────────────────────────────────────────────────────────────────────────

// Phase 10a — POST /api/attempts/{id}/clips replacements for the
// "+ add this clip" action. Reads the attempt's current clip list
// from `attemptsState`, appends one new entry, PATCHes the full list.
// Returns the new clip count on success or null on failure.
async function addClipToActiveAttempt(
  activeAttemptId: string,
  card: { clip_id: string },
  attemptsState: Record<string, { clips: Array<{ clip_id: string; trim_start_offset: number; trim_end_offset: number; internal_pause_max_sec: number | null; notes: string }> }>,
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

// Phase 10a — "no active attempt yet, create one with this clip
// as the seed." POSTs to create, sets the new attempt as active,
// returns the new attempt id (or null on failure).
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

export default function Project() {
  const navigate = useNavigate();
  const { playClip } = usePlayback();
  const { activeAttemptId, setActiveAttemptId } = useActiveAttempt();
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [grid, setGrid] = useState<TakeGridView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedCard, setSelectedCard] = useState<TakeCard | null>(null);

  // Phase 9 — clicking a TakeCard both opens the side panel AND starts
  // preview playback. Spec language: "preview seeks and plays that
  // range from the source video."
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
  const [appAttempts, setAppAttempts] = useState<AppState["attempts"]>({});
  const [generatingAttempts, setGeneratingAttempts] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  // Phase 10a — clear active-attempt context if it's been deleted or
  // belongs to a different project.
  useActiveAttemptValidation(appAttempts, projectId);
  // Phase 8.1 — surface progress while the CTA is firing.
  const premadeProgress = useRunProgress(
    "/api/premade/progress",
    generatingAttempts,
  );

  const loadAppState = useCallback(async () => {
    const r = await fetch("/api/state");
    if (!r.ok) return;
    const s: AppState = await r.json();
    const list = Object.entries(s.projects ?? {}).map(([pid, p]) => ({
      project_id: pid,
      name: p.name,
    }));
    setProjects(list);
    setAppAttempts(s.attempts ?? {});
    if (list.length > 0 && projectId == null) {
      setProjectId(list[0].project_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Phase 10a — onAdd handler for the per-card + button. Adds the
  // clip to the active attempt (or creates a new attempt with this
  // clip if there's no active one).
  const onAddClipToActive = useCallback(
    async (card: TakeCard) => {
      if (!projectId) return;
      if (activeAttemptId && appAttempts[activeAttemptId]) {
        await addClipToActiveAttempt(activeAttemptId, card, appAttempts);
      } else {
        const newId = await createAttemptWithClip(projectId, card);
        if (newId) setActiveAttemptId(newId);
      }
      // Refresh so the side-panel + summary panel reflect the change.
      loadAppState();
    },
    [activeAttemptId, appAttempts, projectId, setActiveAttemptId, loadAppState],
  );
  const addTooltip = activeAttemptId
    ? `Add to attempt #${activeAttemptId}`
    : "Start a new attempt with this clip";

  // Load the project list + attempts once; pick the first project.
  useEffect(() => {
    loadAppState().catch((e) => setLoadError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reload the grid when the active project changes.
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

  // Phase 8: best-plausible-only summary panel. Diagnostic stays on
  // /attempts — see the plan's "diagnostic is exploration, not
  // assembly" decision.
  const { bestAttemptsForProject, totalAttemptsForProject } = useMemo(() => {
    if (!projectId) {
      return { bestAttemptsForProject: [], totalAttemptsForProject: 0 };
    }
    const best: AttemptSummary[] = [];
    let total = 0;
    for (const [aid, att] of Object.entries(appAttempts)) {
      if (att.project_id !== projectId) continue;
      total++;
      if (att.premade_bucket === "diagnostic") continue;
      best.push({
        attempt_id: aid,
        name: att.name,
        source: att.source,
        premade_bucket: att.premade_bucket,
        continuity_score: att.continuity_score,
        clip_count: att.clips.length,
      });
    }
    best.sort(
      (a, b) =>
        (b.continuity_score ?? 0) - (a.continuity_score ?? 0)
        || a.name.localeCompare(b.name),
    );
    return {
      bestAttemptsForProject: best,
      totalAttemptsForProject: total,
    };
  }, [appAttempts, projectId]);

  const generatePremade = useCallback(async () => {
    if (!projectId) return;
    setGeneratingAttempts(true);
    setGenerateError(null);
    try {
      const r = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/premade-attempts`,
        { method: "POST" },
      );
      const body = await r.json();
      if (!r.ok) {
        setGenerateError(
          typeof body.detail === "string" ? body.detail : r.statusText,
        );
        return;
      }
      // Navigate to the Attempts page after a successful run so the
      // user immediately sees what was generated.
      navigate("/attempts");
    } catch (e) {
      setGenerateError(String(e));
    } finally {
      setGeneratingAttempts(false);
    }
  }, [projectId, navigate]);

  // ---- Empty / error states ----

  if (projects == null) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Project</h1>
        <p className="text-neutral-500 text-sm">Loading…</p>
      </section>
    );
  }

  if (projects.length === 0) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Project</h1>
        <p className="text-neutral-400 text-sm">
          No projects yet.{" "}
          <Link to="/brief" className="underline hover:text-white">
            Write a brief
          </Link>{" "}
          to create one — that's where the script lines, sections, and tags
          live that drive this page.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-2xl font-semibold">Project</h1>
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
        {grid && (
          <div className="flex items-center gap-2 text-xs text-neutral-500 ml-auto">
            <Chip label={`${grid.summary.total_tagged} tagged`} />
            {grid.summary.untagged_clips > 0 && (
              <Chip
                label={`${grid.summary.untagged_clips} untagged`}
                tone="amber"
              />
            )}
            {grid.summary.stale_clips > 0 && (
              <Chip
                label={`${grid.summary.stale_clips} stale`}
                tone="amber"
              />
            )}
            <Link
              to="/brief"
              className="rounded-md bg-neutral-800 hover:bg-neutral-700 px-2 py-1"
            >
              Tag clips →
            </Link>
          </div>
        )}
      </div>

      {loadError && (
        <div className="rounded-md border border-red-900 bg-red-950/40 p-3 text-xs text-red-300">
          Failed to load take grid: {loadError}
        </div>
      )}

      {/* Phase 8: attempts summary or CTA. Surfaces above the Take Grid
          so the user can see "what's already generated" before diving
          into per-line scanning. */}
      {grid && totalCardsInGrid > 0 && (
        <>
          {bestAttemptsForProject.length > 0 ? (
            <AttemptsSummaryPanel
              bestAttempts={bestAttemptsForProject}
              totalCount={totalAttemptsForProject}
              onNavigate={() => navigate("/attempts")}
            />
          ) : (
            <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-3 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">
                  No premade attempts yet
                </div>
                <div className="text-xs text-neutral-500">
                  Generate candidate videos from your tagged clips. ~30s.
                </div>
              </div>
              <button
                onClick={generatePremade}
                disabled={generatingAttempts}
                className="text-xs rounded-md bg-white text-neutral-950 font-medium hover:bg-neutral-200 px-3 py-1.5 disabled:opacity-50"
              >
                {generatingAttempts
                  ? "Generating…"
                  : "Generate premade attempts"}
              </button>
            </div>
          )}
          {generatingAttempts && (
            <PremadeProgressPanel info={premadeProgress} />
          )}
          {generateError && (
            <div className="rounded-md border border-red-900 bg-red-950/40 p-3 text-xs text-red-300">
              {generateError}
            </div>
          )}
        </>
      )}

      {grid && totalCardsInGrid === 0 && (
        <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-4 text-sm text-neutral-300">
          <div className="font-medium mb-1">No tags yet for this project.</div>
          <div className="text-neutral-500">
            Head to the{" "}
            <Link to="/brief" className="underline hover:text-white">
              Brief page
            </Link>{" "}
            and hit "Tag clips" to run the LLM tagger over your library.
          </div>
        </div>
      )}

      {grid && totalCardsInGrid > 0 && (
        <div className="grid grid-cols-[1fr_320px] gap-4 items-start">
          <div className="space-y-3 min-w-0">
            <div className="space-y-2">
              {grid.lines.map((row) => (
                <LineRowView
                  key={row.tag_id}
                  row={row}
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
                <BucketSection
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

function Chip({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "amber";
}) {
  const cls =
    tone === "amber"
      ? "bg-amber-950/60 text-amber-300 border-amber-900"
      : "bg-neutral-900 text-neutral-400 border-neutral-800";
  return (
    <span className={`rounded-md border px-2 py-0.5 ${cls}`}>{label}</span>
  );
}
