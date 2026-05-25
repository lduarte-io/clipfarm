# ClipFarm

Personal video studio organized around raw footage as a queryable, AI-indexed library. See [`clipfarm-spec.md`](./clipfarm-spec.md) for the canonical spec and [`PHASES.md`](./PHASES.md) / [`COMPLETED_PHASES.md`](./COMPLETED_PHASES.md) for the build plan.

## Local dev (Phase 1 skeleton)

Prerequisites:

```bash
brew install ollama ffmpeg uv
brew services start ollama
ollama pull llama3.1:8b
```

Backend:

```bash
uv sync
uv run uvicorn clipfarm.app:app --reload --port 8765
```

Frontend (one-time build for the FastAPI-served path):

```bash
cd web && npm install && npm run build
```

Then open [http://localhost:8765/](http://localhost:8765/).

For frontend hot-reload, run `vite dev` in `web/` (proxies `/api/*` to `:8765`).

## Tests

```bash
uv run pytest
```
