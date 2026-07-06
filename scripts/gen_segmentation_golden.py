"""Generates the cross-implementation segmentation golden-master fixture
consumed by CFDomainTests (N3).

Runs the *reference* implementation (segment_words_by_silence, _make_clip_id,
transcript_text_for_range) over a seeded pseudo-random word list and dumps
input + expected output as JSON. The Swift port must reproduce ranges, clip
IDs, and per-clip transcript texts exactly.

Regenerate (only if the reference semantics ever change — they shouldn't;
the web implementation is frozen):

    uv run python scripts/gen_segmentation_golden.py \
        > mac/ClipFarmKit/Tests/CFDomainTests/Resources/segmentation-golden.json
"""
from __future__ import annotations

import json
import random
import sys

from clipfarm.ingest import _make_clip_id
from clipfarm.models import WhisperTranscript
from clipfarm.segmentation import segment_words_by_silence
from clipfarm.transcripts import transcript_text_for_range

STEM = "golden"
SEED = 20260706


def build_words() -> list[dict]:
    rng = random.Random(SEED)
    words: list[dict] = []
    t = 0.25
    for i in range(400):
        duration = round(rng.uniform(0.08, 0.6), 3)
        start = round(t, 3)
        end = round(start + duration, 3)
        words.append({"start": start, "end": end, "word": f" w{i}", "probability": 0.9})
        # Gap mixture: mostly intra-clip, some exact-threshold hits (the
        # load-bearing >= boundary), some large silences, some zero gaps.
        roll = rng.random()
        if roll < 0.70:
            gap = round(rng.uniform(0.0, 1.9), 3)
        elif roll < 0.78:
            gap = 2.0  # exactly the default threshold -> must split
        elif roll < 0.86:
            gap = 0.75  # exactly the second test threshold -> must split there
        elif roll < 0.92:
            gap = 0.0
        else:
            gap = round(rng.uniform(2.1, 9.0), 3)
        t = end + gap
    # Half-even-rounding stress: timestamps landing on .5 ms so the clip-ID
    # encoding exercises int(round(t*1000)) banker's rounding on both sides.
    edge = t + 5.0
    for i, (s, e) in enumerate([(edge, edge + 0.4995), (edge + 3.0005, edge + 3.5015)]):
        words.append({"start": round(s, 4), "end": round(e, 4), "word": f" edge{i}", "probability": 0.9})
    return words


def main() -> None:
    words = build_words()
    duration = words[-1]["end"] + 1.0
    transcript = WhisperTranscript.model_validate(
        {
            "schema_version": 1,
            "duration": duration,
            "segments": [
                {
                    "id": 0,
                    "start": words[0]["start"],
                    "end": words[-1]["end"],
                    "words": words,
                }
            ],
        }
    )
    flat = [w for seg in transcript.segments for w in seg.words]

    cases = []
    for threshold in (2.0, 0.75):
        ranges = segment_words_by_silence(flat, gap_threshold_sec=threshold)
        cases.append(
            {
                "threshold": threshold,
                "ranges": [[s, e] for s, e in ranges],
                "clip_ids": [_make_clip_id(STEM, s, e) for s, e in ranges],
                "texts": [transcript_text_for_range(transcript, s, e) for s, e in ranges],
            }
        )

    json.dump(
        {"stem": STEM, "seed": SEED, "duration": duration, "words": words, "cases": cases},
        sys.stdout,
        indent=1,
    )


if __name__ == "__main__":
    main()
