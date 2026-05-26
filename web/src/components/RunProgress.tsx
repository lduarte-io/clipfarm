import { useEffect, useState } from "react";

// Phase 8.1 — small reusable poller for the /api/*/progress endpoints.
// Used by Brief.tsx (tagging), Attempts.tsx + Project.tsx (premade).
// Three uses → real abstraction, not premature.

export type RunProgress = {
  running: boolean;
  project_id?: string;
  phase?: string;
  // Active LLM provider + model the route chose for this run.
  // Lets the UI show "(anthropic · claude-sonnet-4-6)" so you know
  // which path you're watching.
  provider?: "ollama" | "anthropic";
  model?: string;
  // Tagging fields:
  current_batch?: number;
  total_batches?: number;
  candidates?: number;
  // Premade fields:
  current_strategy?: number;
  total_strategies?: number;
  strategy_name?: string;
  attempt_count?: number;
  // Common:
  elapsed_sec?: number;
};

/**
 * Poll `endpoint` every 2 seconds while `active` is true. Returns
 * the latest response. The hook stops polling automatically when
 * `active` flips false.
 */
export function useRunProgress(
  endpoint: string,
  active: boolean,
): RunProgress | null {
  const [info, setInfo] = useState<RunProgress | null>(null);

  useEffect(() => {
    if (!active) {
      setInfo(null);
      return;
    }
    let cancelled = false;

    const poll = async () => {
      try {
        const r = await fetch(endpoint);
        if (!r.ok) return;
        const body: RunProgress = await r.json();
        if (!cancelled) setInfo(body);
      } catch {
        // Network blip — keep retrying next tick.
      }
    };
    // Fire immediately so the user sees status fast, then every 2s.
    poll();
    const handle = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [endpoint, active]);

  return info;
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase labels — backend emits machine-readable keys; UI maps them here.
// ─────────────────────────────────────────────────────────────────────────────

const TAG_PHASE_LABELS: Record<string, string> = {
  starting: "Starting…",
  preflight: "Preparing batches…",
  batching: "Tagging clips",
  committing: "Saving tags…",
};

const PREMADE_PHASE_LABELS: Record<string, string> = {
  starting: "Starting…",
  preflight: "Preparing strategies…",
  running_strategies: "Building candidate attempts",
  naming: "Naming attempts (LLM)…",
  persisting: "Saving attempts…",
};

function formatElapsed(sec: number | undefined): string {
  if (sec == null || !isFinite(sec)) return "0s";
  const total = Math.round(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function formatEta(elapsed: number, current: number, total: number): string | null {
  if (current <= 0 || total <= current) return null;
  const eta = elapsed * (total - current) / current;
  if (eta < 10) return null; // not worth showing for sub-10s estimates
  return formatElapsed(eta);
}

// ─────────────────────────────────────────────────────────────────────────────
// Render
// ─────────────────────────────────────────────────────────────────────────────

function ProviderChip({ info }: { info: RunProgress }) {
  if (!info.provider) return null;
  return (
    <span
      className="text-[10px] font-mono uppercase tracking-wide rounded px-1.5 py-0.5 bg-neutral-900/60 text-neutral-300 border border-neutral-700"
      title={`Provider: ${info.provider}${info.model ? ` · model: ${info.model}` : ""}`}
    >
      {info.provider}
      {info.model && <span className="text-neutral-500"> · {info.model}</span>}
    </span>
  );
}

export function TagProgressPanel({ info }: { info: RunProgress | null }) {
  if (!info || !info.running) return null;
  const label = TAG_PHASE_LABELS[info.phase ?? ""] ?? info.phase ?? "Running…";
  const elapsed = info.elapsed_sec ?? 0;
  const current = info.current_batch;
  const total = info.total_batches;
  const eta =
    current != null && total != null
      ? formatEta(elapsed, current, total)
      : null;

  let pct = 0;
  if (current != null && total != null && total > 0) {
    pct = Math.min(100, (current / total) * 100);
  } else if (info.phase === "preflight") {
    pct = 5;
  } else if (info.phase === "committing") {
    pct = 95;
  }

  return (
    <div className="rounded-md border border-sky-900 bg-sky-950/40 p-3 space-y-2">
      <div className="flex items-baseline gap-2 text-xs flex-wrap">
        <span className="font-medium text-sky-200">{label}</span>
        <ProviderChip info={info} />
        {current != null && total != null && (
          <span className="text-sky-400 font-mono">
            batch {current} of {total}
          </span>
        )}
        <span className="ml-auto text-sky-400/80 font-mono">
          {formatElapsed(elapsed)}
          {eta && ` · ~${eta} left`}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-sky-950 overflow-hidden">
        <div
          className="h-full bg-sky-500 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function PremadeProgressPanel({ info }: { info: RunProgress | null }) {
  if (!info || !info.running) return null;
  const label =
    PREMADE_PHASE_LABELS[info.phase ?? ""] ?? info.phase ?? "Running…";
  const elapsed = info.elapsed_sec ?? 0;
  const current = info.current_strategy;
  const total = info.total_strategies;

  let pct = 5;
  if (info.phase === "running_strategies" && current != null && total != null) {
    pct = (current / total) * 70; // strategies fill 0-70%
  } else if (info.phase === "naming") {
    pct = 80;
  } else if (info.phase === "persisting") {
    pct = 95;
  }

  return (
    <div className="rounded-md border border-violet-900 bg-violet-950/40 p-3 space-y-2">
      <div className="flex items-baseline gap-2 text-xs flex-wrap">
        <span className="font-medium text-violet-200">{label}</span>
        <ProviderChip info={info} />
        {info.strategy_name && (
          <span className="text-violet-400 font-mono truncate max-w-xs">
            {info.strategy_name}
          </span>
        )}
        {current != null && total != null && info.phase === "running_strategies" && (
          <span className="text-violet-400 font-mono">
            ({current}/{total})
          </span>
        )}
        <span className="ml-auto text-violet-400/80 font-mono">
          {formatElapsed(elapsed)}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-violet-950 overflow-hidden">
        <div
          className="h-full bg-violet-500 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
