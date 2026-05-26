import { useCallback, useEffect, useMemo, useState } from "react";

// Phase 5 brief editor: project list on the left, markdown textarea on
// the right with live parse preview + save / delete buttons.

type ProjectSummary = {
  project_id: string;
  name: string;
  created_at: string;
  line_count: number;
  section_count: number;
  tag_count: number;
};

type ProjectDetail = {
  project_id: string;
  name: string;
  brief_md: string;
  created_at: string;
  script_lines: string[];
  sections: string[];
  tags: string[];
};

type ParsePreview = {
  name: string;
  lines_count: number;
  sections: string[];
  tags: string[];
};

type ParseError = {
  error: string;
  line?: number;
  column?: number;
};

const EXAMPLE_BRIEF = `---
name: your project name
script:
  - First line of the script.
  - Second line.
sections:
  - the hook
  - the why
tags:
  - hook
  - mistakes
---

# What's good

Tone, energy, length notes — anything you want the LLM tagger to bias toward.
`;

const YAML_HELP = `Tips:
- 'name' is required. Everything else is optional.
- Script lines starting with '-', '#', or containing ':' need single
  quotes around them: '- this is a literal dash'.
- Indent list items with 2 spaces under their key.
`;

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
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

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

export default function Brief() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<string | "new" | null>(null);
  const [draftBrief, setDraftBrief] = useState<string>("");
  const [originalBrief, setOriginalBrief] = useState<string>("");
  const [preview, setPreview] = useState<ParsePreview | null>(null);
  const [parseError, setParseError] = useState<ParseError | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const refreshProjects = useCallback(async () => {
    const r = await fetch("/api/projects");
    if (r.ok) setProjects(await r.json());
  }, []);

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  // Load the selected project's brief.
  useEffect(() => {
    if (selected === null) return;
    if (selected === "new") {
      setDraftBrief(EXAMPLE_BRIEF);
      setOriginalBrief("");
      setSaveError(null);
      return;
    }
    let cancelled = false;
    (async () => {
      const r = await fetch(`/api/projects/${encodeURIComponent(selected)}`);
      if (cancelled || !r.ok) return;
      const detail: ProjectDetail = await r.json();
      setDraftBrief(detail.brief_md);
      setOriginalBrief(detail.brief_md);
      setSaveError(null);
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  // Debounced live parse preview.
  useEffect(() => {
    if (!draftBrief.trim()) {
      setPreview(null);
      setParseError(null);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const r = await fetch("/api/projects/parse", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ brief_md: draftBrief }),
        });
        if (!r.ok) {
          const body = await r.json().catch(() => ({ detail: { error: r.statusText } }));
          const detail = body.detail ?? body;
          setPreview(null);
          setParseError(
            typeof detail === "string" ? { error: detail } : detail
          );
          return;
        }
        setPreview(await r.json());
        setParseError(null);
      } catch (e) {
        setParseError({ error: String(e) });
        setPreview(null);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [draftBrief]);

  const dirty = draftBrief !== originalBrief;

  async function saveBrief() {
    if (parseError) {
      setSaveError("Fix the parse error before saving.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const url =
        selected === "new"
          ? "/api/projects"
          : `/api/projects/${encodeURIComponent(selected!)}`;
      const method = selected === "new" ? "POST" : "PATCH";
      const r = await fetch(url, {
        method,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ brief_md: draftBrief }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: { error: r.statusText } }));
        const detail = body.detail ?? body;
        setSaveError(
          typeof detail === "string" ? detail : JSON.stringify(detail)
        );
        return;
      }
      const body = await r.json();
      setOriginalBrief(draftBrief);
      await refreshProjects();
      if (selected === "new") setSelected(body.project_id);
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    const pid = deleteConfirm;
    if (pid === null) return;
    setDeleteConfirm(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(pid)}`, {
        method: "DELETE",
      });
      if (!r.ok) {
        setSaveError(`Delete failed: ${r.status} ${r.statusText}`);
        return;
      }
      await refreshProjects();
      if (selected === pid) {
        setSelected(null);
        setDraftBrief("");
        setOriginalBrief("");
      }
    } catch (e) {
      setSaveError(String(e));
    }
  }

  return (
    <section className="space-y-4 h-[calc(100vh-7rem)] flex flex-col">
      <div>
        <h1 className="text-2xl font-semibold">Brief editor</h1>
        <p className="text-neutral-400 text-sm mt-1">
          Each project is a YAML frontmatter + markdown body. Saving creates or
          updates the project + its script-line and section tags. Edits flip
          all prior <code>clip_project_tags</code> rows to{" "}
          <code>stale: true</code> — re-tag in Phase 6.
        </p>
      </div>

      <div className="flex-1 grid grid-cols-[280px_1fr] gap-4 min-h-0">
        {/* Left rail */}
        <div className="space-y-3 overflow-y-auto pr-2">
          <button
            onClick={() => setSelected("new")}
            className={`w-full rounded-md font-medium px-3 py-2 text-sm ${
              selected === "new"
                ? "bg-white text-neutral-950"
                : "bg-neutral-800 hover:bg-neutral-700 text-white"
            }`}
          >
            + New project
          </button>
          <div className="rounded-md border border-neutral-800 bg-neutral-900/50">
            <div className="px-3 py-2 text-sm font-medium border-b border-neutral-800">
              Projects{" "}
              <span className="text-neutral-500 text-xs font-normal">
                ({projects.length})
              </span>
            </div>
            {projects.length === 0 ? (
              <div className="px-3 py-4 text-xs text-neutral-500">
                None yet. Create one above.
              </div>
            ) : (
              <ul className="divide-y divide-neutral-800">
                {projects.map((p) => (
                  <li key={p.project_id}>
                    <button
                      onClick={() => setSelected(p.project_id)}
                      className={`w-full text-left px-3 py-2 text-xs hover:bg-neutral-900 ${
                        selected === p.project_id ? "bg-neutral-800" : ""
                      }`}
                    >
                      <div className="font-medium truncate">{p.name}</div>
                      <div className="mt-0.5 text-neutral-500 text-[10px]">
                        {p.line_count} lines · {p.section_count} sections ·{" "}
                        {p.tag_count} total tags
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Main panel */}
        <div className="rounded-md border border-neutral-800 bg-neutral-900/30 p-4 min-h-0 flex flex-col">
          {selected === null ? (
            <div className="text-neutral-500 text-sm p-8 text-center my-auto">
              Pick a project on the left, or click "New project" to start one.
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-3 pb-3 border-b border-neutral-800">
                <div className="text-sm">
                  {selected === "new" ? (
                    <span className="font-semibold">New project</span>
                  ) : (
                    <>
                      Editing{" "}
                      <code className="font-semibold">
                        {projects.find((p) => p.project_id === selected)?.name ?? ""}
                      </code>
                      {dirty && (
                        <span className="ml-2 text-amber-300 text-xs">
                          • unsaved
                        </span>
                      )}
                    </>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={saveBrief}
                    disabled={saving || !dirty || !!parseError}
                    className="px-3 py-1.5 text-sm rounded-md bg-white text-neutral-950 font-medium hover:bg-neutral-200 disabled:opacity-50"
                  >
                    {saving ? "Saving…" : selected === "new" ? "Create" : "Save"}
                  </button>
                  {selected !== "new" && (
                    <button
                      onClick={() => setDeleteConfirm(selected)}
                      className="px-3 py-1.5 text-sm rounded-md border border-red-800 text-red-300 hover:bg-red-900/30"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

              <textarea
                value={draftBrief}
                onChange={(e) => setDraftBrief(e.target.value)}
                spellCheck={false}
                className="flex-1 w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-xs font-mono leading-5 resize-none min-h-0"
              />

              <div className="mt-3 space-y-2 text-xs">
                {parseError ? (
                  <div className="text-red-400 whitespace-pre-wrap">
                    <strong>Parse error:</strong> {parseError.error}
                    {parseError.line && (
                      <span>
                        {" "}
                        (line {parseError.line}
                        {parseError.column && `, col ${parseError.column}`})
                      </span>
                    )}
                  </div>
                ) : preview ? (
                  <div className="text-neutral-400">
                    Parsed: <code className="text-neutral-300">{preview.name}</code> ·{" "}
                    {preview.lines_count} line{preview.lines_count === 1 ? "" : "s"} ·{" "}
                    {preview.sections.length} section
                    {preview.sections.length === 1 ? "" : "s"} ·{" "}
                    {preview.tags.length} tag
                    {preview.tags.length === 1 ? "" : "s"}
                  </div>
                ) : (
                  <div className="text-neutral-500">No content yet.</div>
                )}
                {saveError && (
                  <div className="text-red-400 whitespace-pre-wrap">
                    {saveError}
                  </div>
                )}
                <details>
                  <summary className="cursor-pointer text-neutral-500">
                    Brief format help
                  </summary>
                  <pre className="mt-2 p-2 bg-neutral-950 rounded-md text-[11px] whitespace-pre-wrap text-neutral-400">
                    {YAML_HELP}
                  </pre>
                  <details className="mt-2">
                    <summary className="cursor-pointer text-neutral-500">
                      Example
                    </summary>
                    <pre className="mt-2 p-2 bg-neutral-950 rounded-md text-[11px] whitespace-pre-wrap text-neutral-400">
                      {EXAMPLE_BRIEF}
                    </pre>
                  </details>
                </details>
              </div>
            </>
          )}
        </div>
      </div>

      {deleteConfirm !== null && (
        <ConfirmDialog
          title="Delete project?"
          body={`This removes the project and every clip_project_tags row + Attempt for it. You can restore from the .clipfarm/snapshots/ folder if you regret this.`}
          confirmLabel="Delete"
          destructive
          onConfirm={confirmDelete}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </section>
  );
}
