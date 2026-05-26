"""Pydantic models for the ClipFarm data model.

Mirrors the JSON shape documented in `clipfarm-spec.md` ("Data model" section)
and the Whisper sidecar shape documented in "Whisper transcript schema."

Invariants enforced here (see CLAUDE.md → "Data model invariants" + tests):

- Clip IDs are opaque after creation. The encoded `source__start__end` form is
  for human readability only; `start_sec` / `end_sec` may mutate via boundary
  correction without changing the ID.
- All IDs are strings (JSON object keys must be strings).
- `tracks: None` is the v0 default on every clip — schema hook only.
- Categories are a fixed soft-category set, not free text.
- **`extra="ignore"`**: unknown keys in `clipfarm.json` are logged and dropped
  on load (see spec → "Unknown-key tolerance"). Our own writers can't produce
  extras because they round-trip through validated models.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal[
    "on-script",
    "related-but-different",
    "standalone-idea",
    "off-topic",
    "fragment",
]

TagKind = Literal["section", "line", "tag"]
# "section" = a chapter / beat label (parent_id=None)
# "line"    = a script line (parent_id=section's id, or None when the
#             brief doesn't group lines into sections — the v0 default)
# "tag"     = an ad-hoc project-level label from the brief's `tags:` array
#             (parent_id=None, distinct from "line" so update_project's
#             name-keyed merge can tell them apart)

TagSource = Literal["ai", "user", "voice-annotation"]

PremadeBucket = Literal["best", "diagnostic"]


class StrictModel(BaseModel):
    """Base model. `extra="ignore"` drops unknown keys silently at the model
    boundary — the loader is responsible for logging them before validation."""

    model_config = ConfigDict(extra="ignore")


# --- Per-clip media composition hooks (reserved for v0, populated in future) ---


class AudioOverride(StrictModel):
    file_path: str
    start_offset_sec: float = 0.0


class VideoOverride(StrictModel):
    source_id: str
    start_sec: float
    end_sec: float


class Overlay(StrictModel):
    start_sec: float
    end_sec: float
    type: Literal["blackout"] = "blackout"
    color: str = "#000000"


class TracksOverride(StrictModel):
    """Reserved schema hook for per-clip media composition (Future Ideas).

    v0 writers MUST leave this as `None` on every clip. v0 readers tolerate the
    field being populated but treat it the same as `None` — i.e. play the
    source file's audio and video unchanged.
    """

    audio_override: Optional[AudioOverride] = None
    video_override: Optional[VideoOverride] = None
    overlays: list[Overlay] = Field(default_factory=list)


# --- Core entities ---


class Source(StrictModel):
    filename: str
    path: str
    duration_sec: Optional[float] = None
    fps: Optional[float] = None
    transcript_path: Optional[str] = None
    added_at: str
    unavailable: bool = False


class Clip(StrictModel):
    source_id: str
    start_sec: float
    end_sec: float
    transcript_text: str = ""
    derived_from_clip_id: Optional[str] = None
    tracks: Optional[TracksOverride] = None
    created_at: str


class ProjectTag(StrictModel):
    kind: TagKind
    name: str
    parent_id: Optional[str] = None
    order_idx: int = 0


class Script(StrictModel):
    """The script as the user wrote it in the brief. Each entry in `lines`
    becomes a `ProjectTag(kind="line")` in the Project's `tags` dict at
    parse time; this `Script` model is the read-back view.

    Sections live in `Project.tags` as `ProjectTag(kind="section")` entries
    with `parent_id=None`; lines reference their parent section by ID via
    `ProjectTag.parent_id`. The hierarchy is in the tag set, not here.
    """

    lines: list[str] = Field(default_factory=list)


class Project(StrictModel):
    name: str = Field(..., min_length=1)
    brief_md: str = ""
    # Phase 5 — typed Script model replaces the loose dict from the v1
    # data-model example. Brief-less projects (rare; v0 has no UI path to
    # create one) have `script=None`.
    script: Optional[Script] = None
    tags: dict[str, ProjectTag] = Field(default_factory=dict)
    created_at: str


class ClipProjectTag(StrictModel):
    """Many-to-many bridge between clips and projects.

    A clip can carry one entry per project, with its own `(section, line,
    category)` triple. This is what makes ClipFarm a personal idea-engine
    rather than a one-video tool — protected by the uniqueness validator on
    `ClipFarmState` (stubbed at v0, activated in Phase 6).
    """

    clip_id: str
    project_id: str
    project_tag_id: Optional[str] = None
    category: Category
    confidence: float = 1.0
    source: TagSource = "user"
    stale: bool = False
    notes: str = ""


class AttemptClip(StrictModel):
    clip_id: str
    trim_start_offset: float = 0.0
    trim_end_offset: float = 0.0
    # Phase 10 ships a "tighten internal pauses" toggle that sets this to a
    # sensible default (e.g. 0.5s); the resolver collapses interior gaps. The
    # field is per-attempt-clip and never mutates the base clip.
    internal_pause_max_sec: Optional[float] = None
    notes: str = ""


class Attempt(StrictModel):
    project_id: str
    name: str
    parent_attempt_id: Optional[str] = None
    source: Literal["ai-premade", "hand-built", "fork"] = "hand-built"
    # `"best"` / `"diagnostic"` / None. Drives the two-bucket premade UI in
    # Phase 8. Hand-built attempts and forks have `None`.
    premade_bucket: Optional[PremadeBucket] = None
    # Cache: fraction (0.0–1.0) of runtime sourced from one contiguous span in
    # one source video. Recomputed when the clip list changes. On-disk value
    # is a cache — readers should be willing to recompute.
    continuity_score: Optional[float] = None
    clips: list[AttemptClip] = Field(default_factory=list)
    # Set true by boundary correction when a clip an attempt references gets
    # split/deleted/etc. UI surfaces a "review me" banner.
    needs_review: bool = False
    created_at: str


class VoiceAnnotation(StrictModel):
    source_id: str
    timestamp_sec: float
    text: str
    resolved_clip_id: Optional[str] = None
    target_project_id: Optional[str] = None
    target_tag_id: Optional[str] = None


# --- Whisper sidecar (consumed by ingest, declared here so the shape is
#     pinned before Phase 2 reaches for it) ----------------------------------


class WhisperWord(StrictModel):
    start: float
    end: float
    word: str
    probability: Optional[float] = None


class WhisperSegment(StrictModel):
    id: Optional[int] = None
    start: float
    end: float
    text: Optional[str] = None
    words: list[WhisperWord] = Field(default_factory=list)


class WhisperTranscript(StrictModel):
    """The `.whisper.json` sidecar shape. Verified against all 18 files in
    `~/Desktop/.../05.19.26/`. See spec → "Whisper transcript schema."

    Phase 2 ingest validates every sidecar through this model and refuses with
    a clear error if `schema_version` is not 1.
    """

    schema_version: int
    source_filename: Optional[str] = None
    language: Optional[str] = None
    language_probability: Optional[float] = None
    duration: Optional[float] = None
    model: Optional[str] = None
    transcribed_at: Optional[str] = None
    segments: list[WhisperSegment] = Field(default_factory=list)


# --- Top-level state container ------------------------------------------------


class ClipFarmState(StrictModel):
    """The full on-disk `clipfarm.json` shape."""

    version: int = 1
    sources: dict[str, Source] = Field(default_factory=dict)
    clips: dict[str, Clip] = Field(default_factory=dict)
    projects: dict[str, Project] = Field(default_factory=dict)
    clip_project_tags: list[ClipProjectTag] = Field(default_factory=list)
    attempts: dict[str, Attempt] = Field(default_factory=dict)
    voice_annotations: list[VoiceAnnotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_clip_project_tag_uniqueness(self) -> "ClipFarmState":
        """Stubbed at v0 — no tags exist yet. Activated in Phase 6 by removing
        the early return; the rule is `(clip_id, project_id, project_tag_id,
        category)` must be unique across `clip_project_tags`.

        Stub-then-activate means Phase 6 is a one-line change, not a hunt for
        the right enforcement seam.
        """
        if not self.clip_project_tags:
            return self
        # Phase 6: drop the early return below and let the seen-set check fire.
        return self
        # seen: set[tuple[str, str, Optional[str], str]] = set()
        # for t in self.clip_project_tags:
        #     key = (t.clip_id, t.project_id, t.project_tag_id, t.category)
        #     if key in seen:
        #         raise ValueError(
        #             f"duplicate clip_project_tag for {key} — uniqueness "
        #             f"required on (clip_id, project_id, project_tag_id, category)"
        #         )
        #     seen.add(key)
        # return self


def empty_state() -> ClipFarmState:
    """Return a fresh empty state at the current schema version."""
    from clipfarm.migrations import CURRENT_VERSION

    return ClipFarmState(version=CURRENT_VERSION)
