import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

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

type AppState = {
  projects: Record<string, { name: string }>;
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
}: {
  card: TakeCard;
  selected: boolean;
  onSelect: () => void;
}) {
  const tint = selected ? "ring-1 ring-white/60" : "ring-1 ring-neutral-800";
  return (
    <button
      onClick={onSelect}
      className={`w-[220px] shrink-0 text-left rounded-md bg-neutral-900 hover:bg-neutral-800/80 ${tint} p-3 space-y-2 transition-colors`}
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
      <div className="text-xs text-neutral-200 leading-snug line-clamp-3">
        {truncate(card.transcript_text || "(no transcript)", 180)}
      </div>
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Side panel — full transcript + Open-in-Library link
// ───────────────────────────────────────────────────────────────────────────

function SidePanel({
  card,
  onClose,
}: {
  card: TakeCard | null;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  if (!card) {
    return (
      <aside className="rounded-md border border-neutral-800 bg-neutral-950/60 p-4 text-sm text-neutral-500">
        Pick a card to see its full transcript.
      </aside>
    );
  }
  const openInLibrary = () => {
    const params = new URLSearchParams({ source: card.source_id });
    if (card.first_word_index != null) {
      params.set("word", String(card.first_word_index));
    }
    navigate(`/library?${params.toString()}`);
  };
  return (
    <aside className="rounded-md border border-neutral-800 bg-neutral-950/80 p-4 space-y-3 sticky top-4 max-h-[calc(100vh-7rem)] overflow-y-auto">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-xs font-mono text-neutral-400 truncate">
            {card.filename}
          </div>
          <div className="text-xs text-neutral-500 font-mono">
            {formatTimestamp(card.start_sec)} →{" "}
            {formatTimestamp(card.end_sec)}
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-neutral-500 hover:text-white text-xs px-1.5 py-0.5 rounded hover:bg-neutral-800"
          aria-label="Close detail panel"
        >
          ✕
        </button>
      </div>
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
    </aside>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Line row — horizontally scrolling card strip
// ───────────────────────────────────────────────────────────────────────────

function LineRowView({
  row,
  selectedClipId,
  onSelect,
}: {
  row: LineRow;
  selectedClipId: string | null;
  onSelect: (card: TakeCard) => void;
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
}: {
  category: Category;
  cards: TakeCard[];
  selectedClipId: string | null;
  onSelect: (card: TakeCard) => void;
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
              />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Page
// ───────────────────────────────────────────────────────────────────────────

export default function Project() {
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [grid, setGrid] = useState<TakeGridView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedCard, setSelectedCard] = useState<TakeCard | null>(null);

  // Load the project list once; pick the first project as the active one.
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
                  onSelect={setSelectedCard}
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
                  onSelect={setSelectedCard}
                />
              ))}
            </div>
          </div>
          <SidePanel
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
