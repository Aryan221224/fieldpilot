# How FieldPilot uses Tavily

Tavily is called at runtime, from code the agent invokes. These are not offline lookups.

| # | Where | API | What it does | What protects the decision |
|---|---|---|---|---|
| 1 | `search_advisories` tool (`fieldpilot/context.py`) | `/search` | Pulls current local advisories for the finding (e.g. *late blight potato India*) so the brief cites real, dated sources | Results are marked `UNTRUSTED_DATA`. They are quoted, never used to change the twin's decision, and instructions inside them are ignored |
| 2 | `verify_label` tool (`fieldpilot/label.py`) | `/search` restricted to `ppqs.gov.in`, `cibrc.gov.in`, `agriwelfare.gov.in`, then university mirrors (TNAU, PAU, IARI, ICAR) → `/extract` | Finds the CIB&RC "major uses" row for product × crop × pest and reads the registered dose range and waiting period | A deterministic parser reads the row first. Nemotron reads it only if the parser cannot, and **every number it returns must appear verbatim in the extracted text**. A mirror is labelled as a mirror. No row means the SOP verdict is NEEDS_HUMAN, never a guess |
| 3 | SOP gate (`fieldpilot/sop.py`) | (uses #2) | The label's dose range and waiting period become two SOP checks: registered dose, and harvest date against the crop twin's maturity date | A dose outside the registered range makes the verdict BLOCKED |

Every call is logged in the run record (`runs/<run_id>.json` → `advisories`, `label.searched`,
`label.citations`). The console links the source the label was verified against.

## Fill in after a live run (for the submission)

- A real `verify_label` result with its citation URL: live run on 27 Oct 2026, Mancozeb 75% WP on potato against late blight. The CIB&RC row (parsed deterministically, authority `official`) reads "potato late blight 750gm 1000gm 500ltr. 3-5": 1000 g/ha formulation, 500 L water, 3-day waiting period. A proposed 1500 g/ha was therefore flagged as above the registered dose. Source: https://ppqs.gov.in/sites/default/files/fungicides_31.03.2024.pdf
- Number of Tavily calls per field visit and total latency: 2 calls per `verify_label` (1 search, 1 extract), 3.5 to 8.9 s end to end (6.2 s + 2.7 s on the first, cold call; 0.7 s + 2.8 s on the second). `search_advisories` adds one `/search` (about 10.7 s with `search_depth=advanced` in the doctor run).
