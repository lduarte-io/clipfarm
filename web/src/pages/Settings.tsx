import { useCallback, useEffect, useState } from "react";

// Tagging-provider toggle UI. Lets you switch between local Ollama
// (default, free, ~5 min per chrysalis-size run on Llama 3.1 8B) and
// the Anthropic API (paid, ~30s for the same run on Sonnet 4.6).
//
// API key is stored at rest in .clipfarm/settings.json (gitignored).
// GET /api/settings NEVER returns the raw key — only an "is set"
// indicator. Setting a new key replaces the old one.

type TaggingProvider = "ollama" | "anthropic";

type TaggingSettings = {
  provider: TaggingProvider;
  ollama_model: string;
  anthropic_model: string;
  anthropic_api_key_set: boolean;
};

type SettingsView = {
  version: number;
  tagging: TaggingSettings;
  anthropic_model_options: string[];
};

async function loadSettings(): Promise<SettingsView | null> {
  const r = await fetch("/api/settings");
  if (!r.ok) return null;
  return r.json();
}

async function patchSettings(
  body: Partial<TaggingSettings>,
): Promise<SettingsView | null> {
  const r = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) return null;
  return r.json();
}

export default function Settings() {
  const [s, setS] = useState<SettingsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // API key form state.
  const [keyInput, setKeyInput] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);

  const refresh = useCallback(async () => {
    const data = await loadSettings();
    if (data) setS(data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (s == null) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Settings</h1>
        <p className="text-neutral-500">Loading…</p>
      </section>
    );
  }

  const onProviderChange = async (provider: TaggingProvider) => {
    setError(null);
    setInfo(null);
    const updated = await patchSettings({ provider });
    if (updated) setS(updated);
    else setError("Failed to save provider change.");
  };

  const onModelChange = async (field: "ollama_model" | "anthropic_model", value: string) => {
    setError(null);
    setInfo(null);
    const updated = await patchSettings({ [field]: value } as Partial<TaggingSettings>);
    if (updated) setS(updated);
  };

  const setKey = async (test: boolean) => {
    if (!keyInput.trim()) {
      setError("Enter a key first.");
      return;
    }
    setKeyBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await fetch("/api/settings/anthropic-key", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ api_key: keyInput.trim(), test }),
      });
      const body = await r.json();
      if (!r.ok) {
        setError(typeof body.detail === "string" ? body.detail : r.statusText);
        return;
      }
      setS(body);
      setKeyInput("");
      setInfo(test ? "Key tested + saved." : "Key saved (not tested).");
    } catch (e) {
      setError(String(e));
    } finally {
      setKeyBusy(false);
    }
  };

  const clearKey = async () => {
    setKeyBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await fetch("/api/settings/anthropic-key", {
        method: "DELETE",
      });
      const body = await r.json();
      if (r.ok) {
        setS(body);
        setInfo("Anthropic API key cleared.");
      }
    } finally {
      setKeyBusy(false);
    }
  };

  return (
    <section className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-xs text-neutral-500 mt-1">
          Stored in <code className="font-mono">.clipfarm/settings.json</code>{" "}
          (gitignored). The Anthropic API key is plain text in that file at
          rest; never returned by the server API.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-red-900 bg-red-950/40 p-3 text-xs text-red-300 whitespace-pre-wrap">
          {error}
        </div>
      )}
      {info && (
        <div className="rounded-md border border-emerald-900 bg-emerald-950/40 p-3 text-xs text-emerald-200">
          {info}
        </div>
      )}

      <fieldset className="space-y-3 rounded-md border border-neutral-800 bg-neutral-950/40 p-4">
        <legend className="text-sm font-medium px-1">Tagging + naming LLM</legend>

        <div className="space-y-2 text-sm">
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="radio"
              name="provider"
              checked={s.tagging.provider === "ollama"}
              onChange={() => onProviderChange("ollama")}
              className="mt-1"
            />
            <span>
              <span className="font-medium">Local Ollama</span>
              <span className="text-xs text-neutral-500 block">
                Free. Runs on your machine. Slower (~5 min per 150 clips on
                Llama 3.1 8B). No external network calls.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="radio"
              name="provider"
              checked={s.tagging.provider === "anthropic"}
              onChange={() => onProviderChange("anthropic")}
              className="mt-1"
            />
            <span>
              <span className="font-medium">Anthropic API</span>
              <span className="text-xs text-neutral-500 block">
                Paid (per-token). Much faster (~30s for the same run on
                Sonnet 4.6) and noticeably smarter at picking script lines.
                Requires an API key.
              </span>
            </span>
          </label>
        </div>

        {s.tagging.provider === "ollama" && (
          <div className="space-y-1 pt-2 border-t border-neutral-800">
            <label className="text-xs text-neutral-400 block">
              Ollama model
            </label>
            <input
              type="text"
              value={s.tagging.ollama_model}
              onChange={(e) => setS({ ...s, tagging: { ...s.tagging, ollama_model: e.target.value } })}
              onBlur={(e) => onModelChange("ollama_model", e.target.value)}
              className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs font-mono"
              placeholder="llama3.1:8b"
            />
            <p className="text-[10px] text-neutral-500">
              Must match an Ollama model you've already <code className="font-mono">ollama pull</code>'d.
            </p>
          </div>
        )}

        {s.tagging.provider === "anthropic" && (
          <div className="space-y-3 pt-2 border-t border-neutral-800">
            <div className="space-y-1">
              <label className="text-xs text-neutral-400 block">
                Anthropic model
              </label>
              <select
                value={s.tagging.anthropic_model}
                onChange={(e) => onModelChange("anthropic_model", e.target.value)}
                className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs font-mono"
              >
                {s.anthropic_model_options.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
                {!s.anthropic_model_options.includes(s.tagging.anthropic_model) && (
                  <option value={s.tagging.anthropic_model}>
                    {s.tagging.anthropic_model} (custom)
                  </option>
                )}
              </select>
            </div>

            <div className="space-y-1">
              <label className="text-xs text-neutral-400 block">
                API key{" "}
                {s.tagging.anthropic_api_key_set ? (
                  <span className="text-emerald-400">· set</span>
                ) : (
                  <span className="text-amber-400">· not set</span>
                )}
              </label>
              <input
                type="password"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                placeholder={
                  s.tagging.anthropic_api_key_set
                    ? "enter a new key to replace"
                    : "sk-ant-…"
                }
                className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs font-mono"
                disabled={keyBusy}
                autoComplete="off"
                spellCheck={false}
              />
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => setKey(true)}
                  disabled={keyBusy || !keyInput.trim()}
                  className="rounded-md bg-white text-neutral-950 font-medium px-3 py-1 text-xs hover:bg-neutral-200 disabled:opacity-50"
                >
                  {keyBusy ? "Saving…" : "Set + test"}
                </button>
                <button
                  onClick={() => setKey(false)}
                  disabled={keyBusy || !keyInput.trim()}
                  className="rounded-md border border-neutral-700 text-neutral-200 px-3 py-1 text-xs hover:bg-neutral-800 disabled:opacity-50"
                  title="Save without making a test API call"
                >
                  Set without test
                </button>
                {s.tagging.anthropic_api_key_set && (
                  <button
                    onClick={clearKey}
                    disabled={keyBusy}
                    className="ml-auto rounded-md border border-red-900 text-red-300 px-3 py-1 text-xs hover:bg-red-900/30 disabled:opacity-50"
                  >
                    Clear key
                  </button>
                )}
              </div>
              <p className="text-[10px] text-neutral-500 pt-1">
                The "Set + test" button makes a tiny API call (~6 tokens) to
                verify the key works with the selected model before saving.
              </p>
            </div>
          </div>
        )}
      </fieldset>
    </section>
  );
}
