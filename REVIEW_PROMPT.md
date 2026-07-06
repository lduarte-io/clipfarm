# REVIEW_PROMPT — the canonical cold-review prompt

**How this file is used.** The `/run-phase` coordinator spawns a fresh reviewer agent whose prompt is this file's contents with `{PHASE}` substituted (e.g. `N0`). The coordinator MUST NOT add its own summary, framing, or narrative of what was implemented — no "the implementer built X, please check Y." The reviewer reads the repo cold, exactly like Lillian's manual fresh-session reviews. That context isolation is *why* those reviews find things the implementer's self-review doesn't. Paraphrasing the prompt or pre-framing the review defeats the design.

---

## The prompt (Lillian's, verbatim — never paraphrase or "improve" it)

> Please thoroughly review phase {PHASE}. Give it a through review for code cleanliness, potential bugs, alignment to spec / decisions, architectural clarity and just an overall holistic, solid review.

---

## Standing context for the reviewer (appended after the verbatim prompt, nothing else)

You are a **cold reviewer** with zero implementation context, by design. Build your own picture from the repo:

- `COMPLETED_PHASES.md` — the {PHASE} closeout entry is your review anchor (it is written to be reviewed against the spec).
- `clipfarm-spec.md` — canonical product behavior; the 2026-07-05 native amendment set at the top wins over older text.
- `NATIVE_REWRITE_PLAN.md` — the {PHASE} entry and §2 target architecture.
- `NATIVE_REWRITE_DECISIONS.md` — LOCKED/RESOLVED outcomes are settled; do not relitigate preferences, but DO flag a decision whose factual premise you can falsify or whose consequences the code doesn't honor.
- `mac/CLAUDE.md` — binding build rules and invariants.
- The code under `mac/` this phase touched — read it yourself. Run `cd mac/ClipFarmKit && swift test` if it helps you verify a claim.

Rules:
- **Read-only.** Do not edit files, do not fix anything, do not commit.
- **Agent tool: retrieval helper only.** You may run at most ONE read-only retrieval helper at a time (`subagent_type: "Explore"`, `model: "sonnet"`) for locating code/usages, web/API documentation lookups, or log sweeps. It must not write anything or spawn agents of its own, and you must never use it to read, summarize, or interpret the binding documents listed above on your behalf — those you read first-hand, in full, yourself. All other work you do yourself in this session.
- The Python implementation at the repo root is the **reference, not the oracle** — adjudicate any divergence against the spec.

Output — a severity-ranked findings list. For each finding:

`[severity] · file:line (or doc + section) · what's wrong or risky · evidence · proposed fix`

Severities: `BLOCKER` (wrong behavior / invariant violation / spec drift) · `MAJOR` (bug or architectural problem worth fixing before the next phase) · `MINOR` (cleanliness, clarity, small hazards) · `NIT`. End with a short **clean bill** list: load-bearing things you specifically checked and found solid. Your final message is consumed by the coordinator — findings and clean bill only, no preamble.
