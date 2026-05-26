import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  PremadeProgressPanel,
  useRunProgress,
} from "../components/RunProgress";
import { SidePanel } from "../components/SidePanel";
import { usePlayback } from "../playback/context";

// Phase 8 — Attempts page. Lists generated + hand-built attempts for
// the active project, grouped into two buckets:
//
//   - Best plausible: source="ai-premade" with premade_bucket="best",
//     plus hand-built and forks (premade_bucket=null).
//   - Diagnostic: premade_bucket="diagnostic" only.
//
// Selecting a card opens a side panel showing the ordered clip list.
// Side panel pattern matches Project.tsx + ScriptTOC.tsx — Phase 9's
// live-preview <video> will swap into this same panel (becoming the
// third use → extraction trigger then, not now).

type AttemptClip = {
  clip_id: string;
  trim_start_offset: number;
  trim_end_offset: number;
  internal_pause_max_sec: number | null;
  notes: string;
};

type Attempt = {
  project_id: string;
  name: string;
  parent_attempt_id: string | null;
  source: "ai-premade" | "hand-built" | "fork";
  premade_bucket: "best" | "diagnostic" | null;
  continuity_score: number | null;
  clips: AttemptClip[];
  needs_review: boolean;
  created_at: string;
};

type Clip = {
  source_id: string;
  start_sec: number;
  end_sec: number;
  transcript_text: string;
};

type SourceRec = { filename: string };

type AppState = {
  sources: Record<string, SourceRec>;
  clips: Record<string, Clip>;
  projects: Record<string, { name: string }>;
  attempts: Record<string, Attempt>;
};

type ProjectListItem = { project_id: string; name: string };

type PremadeResponse = {
  generated_count: number;
  replaced_count: number;
  new_attempt_ids: string[];
  naming_source: "llm" | "canned" | "mixed";
  reason: string;
  attempts: Record<string, Attempt>;
};

const SOURCE_BADGE: Record<Attempt["source"], string> = {
  "ai-premade": "bg-violet-900/40 text-violet-300 border-violet-800",
  "hand-built": "bg-sky-900/40 text-sky-300 border-sky-800",
  "fork": "bg-amber-900/40 text-amber-300 border-amber-800",
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

function formatRuntime(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m${String(s).padStart(2, "0")}s`;
}

// Continuity color: green / amber / red. Match the bar fill so users
// don't have to read percentages to feel which attempts are coherent.
function continuityTone(score: number | null): {
  bar: string;
  label: string;
} {
  if (score == null) return { bar: "bg-neutral-700", label: "—" };
  const pct = Math.round(score * 100);
  if (score >= 0.8) return { bar: "bg-emerald-500", label: `${pct}%` };
  if (score >= 0.4) return { bar: "bg-amber-500", label: `${pct}%` };
  return { bar: "bg-red-500", label: `${pct}%` };
}

function clipRuntime(state: AppState, ac: AttemptClip): number {
  const clip = state.clips[ac.clip_id];
  if (!clip) return 0;
  const base = clip.end_sec - clip.start_sec;
  return Math.max(0, base - ac.trim_start_offset - ac.trim_end_offset);
}

function attemptRuntime(state: AppState, att: Attempt): number {
  return att.clips.reduce((sum, ac) => sum + clipRuntime(state, ac), 0);
}

// ───────────────────────────────────────────────────────────────────────────
// Card
// ───────────────────────────────────────────────────────────────────────────

function AttemptCard({
  attemptId,
  attempt,
  state,
  selected,
  onSelect,
}: {
  attemptId: string;
  attempt: Attempt;
  state: AppState;
  selected: boolean;
  onSelect: () => void;
}) {
  const runtime = attemptRuntime(state, attempt);
  const tone = continuityTone(attempt.continuity_score);
  const tint = selected ? "ring-1 ring-white/60" : "ring-1 ring-neutral-800";
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left rounded-md bg-neutral-900 hover:bg-neutral-800/80 ${tint} p-3 space-y-2 transition-colors`}
    >
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium text-neutral-100 flex-1 min-w-0">
          {attempt.name}
        </span>
        <span className="text-[10px] font-mono text-neutral-600 shrink-0">
          #{attemptId}
        </span>
      </div>
      <div className="flex items-center gap-2 text-[10px]">
        <span
          className={`px-1.5 py-0.5 rounded border ${SOURCE_BADGE[attempt.source]}`}
        >
          {attempt.source}
        </span>
        {attempt.premade_bucket && (
          <span className="px-1.5 py-0.5 rounded border bg-neutral-800 text-neutral-400 border-neutral-700">
            {attempt.premade_bucket}
          </span>
        )}
        <span className="text-neutral-500">
          {attempt.clips.length} clips · {formatRuntime(runtime)}
        </span>
        {attempt.needs_review && (
          <span className="text-amber-400" title="A referenced clip was edited; review the assembly.">
            review
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 rounded-full bg-neutral-800 overflow-hidden">
          <div
            className={`h-full ${tone.bar}`}
            style={{
              width: `${(attempt.continuity_score ?? 0) * 100}%`,
            }}
          />
        </div>
        <span className="text-[10px] font-mono text-neutral-400 w-10 text-right">
          {tone.label}
        </span>
      </div>
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Side panel — full clip list for the selected attempt
// ───────────────────────────────────────────────────────────────────────────

function AttemptSidePanel({
  attemptId,
  attempt,
  state,
  onClose,
}: {
  attemptId: string | null;
  attempt: Attempt | null;
  state: AppState;
  onClose: () => void;
}) {
  const tone = attempt ? continuityTone(attempt.continuity_score) : null;
  const runtime = attempt ? attemptRuntime(state, attempt) : 0;
  return (
    <SidePanel
      open={attempt != null && attemptId != null}
      emptyMessage="Pick an attempt to see its full clip list."
      onClose={onClose}
      header={
        attempt && attemptId && (
          <>
            <div className="text-sm font-semibold text-neutral-100">
              {attempt.name}
            </div>
            <div className="text-[10px] font-mono text-neutral-500 mt-0.5">
              #{attemptId} · {attempt.source}
              {attempt.premade_bucket && ` · ${attempt.premade_bucket}`}
            </div>
          </>
        )
      }
    >
      {attempt && tone && (
        <>
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-neutral-500">
              {attempt.clips.length} clips · {formatRuntime(runtime)}
            </span>
            <span className="text-neutral-600 font-mono">·</span>
            <span className="text-neutral-400">continuity {tone.label}</span>
          </div>
          <ol className="space-y-1.5 text-xs">
            {attempt.clips.map((ac, i) => {
              const clip = state.clips[ac.clip_id];
              const filename = clip
                ? state.sources[clip.source_id]?.filename ?? "?"
                : "(clip missing)";
              return (
                <li
                  key={`${ac.clip_id}-${i}`}
                  className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1.5"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="text-neutral-600 tabular-nums w-6 shrink-0">
                      {String(i + 1).padStart(2, "0")}.
                    </span>
                    <span className="font-mono text-neutral-400 truncate flex-1 min-w-0">
                      {filename}
                    </span>
                    {clip && (
                      <span className="text-neutral-500 font-mono text-[10px] shrink-0">
                        {formatTimestamp(clip.start_sec)}–
                        {formatTimestamp(clip.end_sec)}
                      </span>
                    )}
                  </div>
                  {clip?.transcript_text && (
                    <div className="text-neutral-300 leading-snug mt-1 line-clamp-2">
                      {clip.transcript_text}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </SidePanel>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Regenerate confirmation modal — variable-count copy (plan advisory #4)
// ───────────────────────────────────────────────────────────────────────────

function RegenerateModal({
  existingCount,
  busy,
  onConfirm,
  onCancel,
}: {
  existingCount: number;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-4 max-w-md space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold">Regenerate premade attempts?</h3>
        <p className="text-sm text-neutral-300">
          This will replace the {existingCount} existing AI-generated{" "}
          {existingCount === 1 ? "attempt" : "attempts"}. Hand-built attempts
          and forks are not touched.
        </p>
        <p className="text-xs text-neutral-500">
          Naming uses your local Ollama; if it's unreachable, canned names are
          used as a fallback. Takes ~30 seconds.
        </p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md border border-neutral-700 hover:bg-neutral-800 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-white text-neutral-950 font-medium hover:bg-neutral-200 disabled:opacity-50"
          >
            {busy ? "Generating…" : "Regenerate"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Page
// ───────────────────────────────────────────────────────────────────────────

export default function Attempts() {
  const [appState, setAppState] = useState<AppState | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const { playAttempt } = usePlayback();

  // Phase 9 — clicking an attempt opens the side panel AND starts
  // playing through its resolved clip queue.
  const onAttemptSelect = useCallback(
    (aid: string, name: string) => {
      setSelected(aid);
      playAttempt(aid, `attempt #${aid} · ${name}`);
    },
    [playAttempt],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRegenModal, setShowRegenModal] = useState(false);
  const [lastRunInfo, setLastRunInfo] = useState<PremadeResponse | null>(null);
  // Phase 8.1 — poll /api/premade/progress while generating.
  const premadeProgress = useRunProgress("/api/premade/progress", busy);

  const refresh = useCallback(async () => {
    const r = await fetch("/api/state");
    if (!r.ok) {
      setError(`Failed to load state: ${r.status}`);
      return;
    }
    const s: AppState = await r.json();
    setAppState(s);
    const list = Object.entries(s.projects ?? {}).map(([pid, p]) => ({
      project_id: pid,
      name: p.name,
    }));
    setProjects(list);
    if (list.length > 0 && projectId == null) {
      setProjectId(list[0].project_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Partition attempts for the active project.
  const { bestAttempts, diagnosticAttempts, allAttemptCount } = useMemo(() => {
    if (!appState || !projectId) {
      return { bestAttempts: [], diagnosticAttempts: [], allAttemptCount: 0 };
    }
    const best: [string, Attempt][] = [];
    const diag: [string, Attempt][] = [];
    for (const [aid, att] of Object.entries(appState.attempts ?? {})) {
      if (att.project_id !== projectId) continue;
      if (att.premade_bucket === "diagnostic") diag.push([aid, att]);
      else best.push([aid, att]);
    }
    // Sort by continuity desc, then name.
    const sortFn = (a: [string, Attempt], b: [string, Attempt]) =>
      (b[1].continuity_score ?? 0) - (a[1].continuity_score ?? 0)
      || a[1].name.localeCompare(b[1].name);
    best.sort(sortFn);
    diag.sort(sortFn);
    return {
      bestAttempts: best,
      diagnosticAttempts: diag,
      allAttemptCount: best.length + diag.length,
    };
  }, [appState, projectId]);

  const aiPremadeCount = useMemo(() => {
    if (!appState || !projectId) return 0;
    return Object.values(appState.attempts ?? {}).filter(
      (a) => a.project_id === projectId && a.source === "ai-premade",
    ).length;
  }, [appState, projectId]);

  const runGenerate = useCallback(async () => {
    if (!projectId) return;
    setShowRegenModal(false);
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/premade-attempts`,
        { method: "POST" },
      );
      const body = await r.json();
      if (!r.ok) {
        setError(typeof body.detail === "string" ? body.detail : r.statusText);
        return;
      }
      setLastRunInfo(body);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  // ---- Empty / loading states ----

  if (projects == null) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Attempts</h1>
        <p className="text-neutral-500 text-sm">Loading…</p>
      </section>
    );
  }

  if (projects.length === 0) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Attempts</h1>
        <p className="text-neutral-400 text-sm">
          No projects yet.{" "}
          <Link to="/brief" className="underline hover:text-white">
            Write a brief
          </Link>{" "}
          to create one — then tag clips and come back here to generate
          candidate videos.
        </p>
      </section>
    );
  }

  const selectedAttempt =
    selected && appState ? appState.attempts[selected] ?? null : null;

  return (
    <section className="space-y-4">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-2xl font-semibold">Attempts</h1>
        {projects.length > 1 ? (
          <select
            value={projectId ?? ""}
            onChange={(e) => {
              setProjectId(e.target.value);
              setSelected(null);
            }}
            className="rounded-md bg-neutral-900 border border-neutral-700 px-2 py-1 text-sm"
          >
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-sm text-neutral-300">{projects[0].name}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {aiPremadeCount > 0 ? (
            <button
              onClick={() => setShowRegenModal(true)}
              disabled={busy}
              className="text-xs rounded-md border border-neutral-700 hover:bg-neutral-800 px-3 py-1.5 disabled:opacity-50"
            >
              {busy ? "Generating…" : "Regenerate premade attempts"}
            </button>
          ) : (
            <button
              onClick={runGenerate}
              disabled={busy}
              className="text-xs rounded-md bg-white text-neutral-950 font-medium hover:bg-neutral-200 px-3 py-1.5 disabled:opacity-50"
            >
              {busy ? "Generating…" : "Generate premade attempts"}
            </button>
          )}
        </div>
      </div>

      {busy && <PremadeProgressPanel info={premadeProgress} />}

      {error && (
        <div className="rounded-md border border-red-900 bg-red-950/40 p-3 text-xs text-red-300 whitespace-pre-wrap">
          {error}
        </div>
      )}

      {lastRunInfo && lastRunInfo.generated_count === 0 && (
        <div className="rounded-md border border-amber-900 bg-amber-950/30 p-3 text-xs text-amber-200">
          No attempts generated. {lastRunInfo.reason}
        </div>
      )}

      {lastRunInfo && lastRunInfo.generated_count > 0 && (
        <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-3 text-xs text-neutral-300">
          Generated <strong>{lastRunInfo.generated_count}</strong> attempts
          {lastRunInfo.replaced_count > 0 &&
            ` (replaced ${lastRunInfo.replaced_count} previous AI-generated)`}
          {" · "}
          naming: <strong>{lastRunInfo.naming_source}</strong>
          {lastRunInfo.naming_source === "canned" && (
            <span className="text-neutral-500">
              {" "}(Ollama unreachable or returned no usable names; names are spec defaults)
            </span>
          )}
        </div>
      )}

      {allAttemptCount === 0 && !busy && (
        <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-4 text-sm text-neutral-300">
          <div className="font-medium mb-1">No attempts for this project yet.</div>
          <div className="text-neutral-500">
            First, tag clips from the{" "}
            <Link to="/brief" className="underline hover:text-white">
              Brief page
            </Link>
            . Then click "Generate premade attempts" above.
          </div>
        </div>
      )}

      {appState && allAttemptCount > 0 && (
        <div className="grid grid-cols-[1fr_360px] gap-4 items-start">
          <div className="space-y-5 min-w-0">
            <section className="space-y-2">
              <h2 className="text-xs uppercase tracking-wide text-neutral-500">
                Best plausible · {bestAttempts.length}
              </h2>
              {bestAttempts.length === 0 ? (
                <div className="text-xs text-neutral-600 italic px-1 py-2">
                  None yet.
                </div>
              ) : (
                <div className="space-y-2">
                  {bestAttempts.map(([aid, att]) => (
                    <AttemptCard
                      key={aid}
                      attemptId={aid}
                      attempt={att}
                      state={appState}
                      selected={selected === aid}
                      onSelect={() => onAttemptSelect(aid, att.name)}
                    />
                  ))}
                </div>
              )}
            </section>

            <section className="space-y-2">
              <h2 className="text-xs uppercase tracking-wide text-neutral-500">
                Diagnostic · {diagnosticAttempts.length}
              </h2>
              <p className="text-[11px] text-neutral-600">
                Browse-only groupings — patterns in how you recorded, not
                ship-ready candidates.
              </p>
              {diagnosticAttempts.length === 0 ? (
                <div className="text-xs text-neutral-600 italic px-1 py-2">
                  None yet. Diagnostic groupings appear when your recording
                  history shows clusterable patterns (e.g., several takes
                  that opened with the same line).
                </div>
              ) : (
                <div className="space-y-2">
                  {diagnosticAttempts.map(([aid, att]) => (
                    <AttemptCard
                      key={aid}
                      attemptId={aid}
                      attempt={att}
                      state={appState}
                      selected={selected === aid}
                      onSelect={() => onAttemptSelect(aid, att.name)}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>
          <AttemptSidePanel
            attemptId={selected}
            attempt={selectedAttempt}
            state={appState}
            onClose={() => setSelected(null)}
          />
        </div>
      )}

      {showRegenModal && (
        <RegenerateModal
          existingCount={aiPremadeCount}
          busy={busy}
          onConfirm={runGenerate}
          onCancel={() => setShowRegenModal(false)}
        />
      )}
    </section>
  );
}
