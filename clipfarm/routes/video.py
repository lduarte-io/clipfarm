"""GET /api/sources/{source_id}/video — Phase 9 source-file streaming
with HTTP Range support.

Browsers driving an HTML5 `<video>` element issue `Range:` requests to
seek without re-downloading. Starlette's `FileResponse` doesn't honor
Range natively, so this route rolls a small custom response.

### Range header forms (locked Phase 9 plan-review #3)

- `bytes=N-M` (closed) — return bytes `[N, M]` inclusive. 206.
- `bytes=N-` (open-ended, "from N to EOF") — what browsers actually use
  for video streaming. Return bytes `[N, total-1]`. 206.
- `bytes=-N` (suffix) — **rejected with 416.** `<video>` never uses it;
  parsing surface for no payoff.
- Multi-range (`bytes=0-99,200-299`) — **rejected with 416.** Same reason.

No Range header at all → 200 with the full body, `Accept-Ranges: bytes`
advertised so the browser knows to issue Range requests on subsequent
seeks.

### HTTP status mapping

- **200** — full response, no Range header.
- **206** — partial content, valid Range header.
- **404** — unknown `source_id`.
- **410** — `source.unavailable=True` (file moved/deleted since ingest).
- **416** — unsupported Range form, range past EOF, or malformed.

### Content-Type by extension

- `.mov` / `.mp4` / `.m4v` → `video/mp4`
- `.mkv` → `video/x-matroska`

These are the four extensions Phase 2 ingest accepts.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse

from clipfarm.models import ClipFarmState
from clipfarm.routes.deps import get_state

log = logging.getLogger("clipfarm.routes.video")

router = APIRouter(prefix="/api", tags=["video"])

# 64KB chunks — standard for video streaming; smaller and the syscall
# overhead dominates, larger wastes memory on each in-flight request.
CHUNK_SIZE = 64 * 1024

_MIME_BY_EXT: dict[str, str] = {
    ".mov": "video/mp4",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
}


# Closed `bytes=N-M` or open-ended `bytes=N-`. The `M` group is optional.
# Suffix `bytes=-N` and multi-range (`,`) are NOT matched — they fall
# through to the "unsupported" path and return 416.
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d+)?$")


def _content_type_for(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _stream_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    """Yield bytes from `path[start:end+1]` in CHUNK_SIZE-sized chunks.

    `end` is INCLUSIVE per RFC 7233. Caller ensures `0 <= start <= end
    < file_size`.
    """
    remaining = end - start + 1
    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break  # short read = file truncated; bail
            yield chunk
            remaining -= len(chunk)


def _range_not_satisfiable(total: int) -> Response:
    """416 with `Content-Range: bytes */total` per RFC 7233 §4.4."""
    return Response(
        status_code=416,
        headers={"Content-Range": f"bytes */{total}"},
    )


@router.get("/sources/{source_id}/video")
def get_source_video(
    source_id: str,
    request: Request,
    state: ClipFarmState = Depends(get_state),
) -> Response:
    source = state.sources.get(source_id)
    if source is None:
        return Response(
            status_code=404,
            content=f"unknown source_id: {source_id}",
            media_type="text/plain",
        )
    if source.unavailable:
        return Response(
            status_code=410,
            content=(
                f"source {source_id} ({source.filename}) is unavailable — "
                f"file moved or deleted since ingest"
            ),
            media_type="text/plain",
        )

    path = Path(source.path)
    if not path.is_file():
        # Source exists in state but the file is gone. The ingest check
        # at startup should have flagged this as `unavailable=True`, but
        # defense-in-depth: same 410 here.
        return Response(
            status_code=410,
            content=(
                f"source {source_id} file not found on disk at {source.path}"
            ),
            media_type="text/plain",
        )

    file_size = path.stat().st_size
    content_type = _content_type_for(path)
    range_header: Optional[str] = request.headers.get("range")

    common_headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        # Disable caching during dogfood — file content changes if the
        # source is replaced; cached responses would mask that.
        "Cache-Control": "no-store",
    }

    if range_header is None:
        # No Range header → full body, 200.
        return StreamingResponse(
            _stream_range(path, 0, file_size - 1),
            status_code=200,
            media_type=content_type,
            headers={
                **common_headers,
                "Content-Length": str(file_size),
            },
        )

    # Reject unsupported forms early — suffix `bytes=-N` and
    # multi-range `bytes=0-9,20-29` both fail the closed/open regex.
    match = _RANGE_RE.match(range_header.strip())
    if match is None:
        log.info(
            "video: unsupported Range header %r for source %s — 416",
            range_header, source_id,
        )
        return _range_not_satisfiable(file_size)

    start = int(match.group(1))
    end_group = match.group(2)
    end = int(end_group) if end_group is not None else file_size - 1

    # Validate bounds.
    if start < 0 or start > end or end >= file_size:
        log.info(
            "video: out-of-range Range %r (file_size=%d) for source %s — 416",
            range_header, file_size, source_id,
        )
        return _range_not_satisfiable(file_size)

    content_length = end - start + 1
    return StreamingResponse(
        _stream_range(path, start, end),
        status_code=206,
        media_type=content_type,
        headers={
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
        },
    )
