# Graduated Confidence Consensus

A reusable GenLayer Intelligent Contract primitive for assessments that
are inherently a **spectrum**, not a binary fact — quality scores, risk
ratings, code review scores, content severity scoring, and similar
graded judgements.

## Live deployment (GenLayer Bradbury testnet)

This contract is deployed and verified working on the **Bradbury**
testnet, not just tested locally in isolation. The repository in this
submission is the exact source deployed at the address below.

| | |
|---|---|
| Network | GenLayer Bradbury Testnet |
| Contract address | `0x709Bfac0b4012F24C7701F64B375CBF02Fd60078` |
| Explorer | [explorer-bradbury.genlayer.com/address/0x709Bfac0b4012F24C7701F64B375CBF02Fd60078](https://explorer-bradbury.genlayer.com/address/0x709Bfac0b4012F24C7701F64B375CBF02Fd60078) |

Anyone can independently verify the deployment and re-run
`create_assessment` / `submit_score` against this address through the
explorer or GenLayer Studio pointed at Bradbury.

## Why this is a distinct primitive

Forcing validators to produce byte-identical numeric scores for a
graded judgement is unrealistic — two independent LLM evaluations of
"how risky is this proposal, 0-100" landing on 61 and 64 are not in
disagreement, they're normal evaluative noise. A contract that treats
that as non-equivalent either never finalizes, or pushes builders
toward trivial yes/no questions instead of the graded judgements that
are actually useful on-chain.

Graduated Confidence Consensus solves this by separating the **strict**
part of the judgement from the **fuzzy** part:
- Validators must agree exactly on the discrete **tier** the score
  falls into (`low` / `medium` / `high`) — this is what downstream
  contracts act on, so it's held to strict agreement.
- The raw numeric **score** may diverge by a configurable tolerance
  (default 12 points) and still be accepted, as long as it stays in
  the same tier.
- Scores that land in different tiers are rejected as non-equivalent
  even if numerically close (e.g. 39 vs 40 straddling a tier boundary
  must NOT pass), so tier boundaries genuinely gate consensus.

The contract also keeps a running `min_score` / `max_score` per
assessment instead of collapsing history into a single number, so
downstream readers can see both the agreed category and how much
evaluative spread exists around it.

## Consensus design

`submit_score()` runs a non-deterministic block that prompts the LLM
with the subject and scoring criteria, returning a JSON object with a
numeric `score` and `justification`. The contract derives a `tier`
from the score locally.

`gl.eq_principle.prompt_comparative` is used with a principle that
explicitly states the tier boundaries and the exact equivalence rule:
tier must match exactly; score may differ within tolerance but only if
both scores remain in the same tier; justification wording is free to
vary. This is a genuinely non-trivial, custom equivalence rule — not a
default strict-equality check and not a thin "ask the LLM and accept
whatever it says" wrapper.

## Public interface

| Method | Type | Description |
|---|---|---|
| `create_assessment(assessment_id, subject, criteria)` | write | Registers a subject to be scored, with criteria describing what high/low scores mean. |
| `submit_score(assessment_id)` | write | Runs a graded LLM evaluation, reaches consensus on the tier, appends to history. |
| `get_current_assessment(assessment_id)` | view | Latest tier, score count, min/max range. |
| `get_history(assessment_id)` | view | Full append-only score history. |
| `get_average_score(assessment_id)` | view | Average score across all recorded entries. |
| `list_assessment_ids()` | view | All tracked assessment ids. |

## Example use cases

- **Grant/proposal risk scoring** for a DAO, where funding tiers (low
  / medium / high risk) gate different approval workflows.
- **Freelance work quality scoring** for escrow release — release
  funds automatically if quality lands in the `high` tier, flag for
  human review on `medium`, dispute on `low`.
- **Content moderation severity scoring** — route to different queues
  by tier rather than a binary allow/block decision.
- **Code review quality gates** in a CI-adjacent on-chain workflow.

## Implementation notes (from real Bradbury testing)

This contract reuses the same SDK patterns confirmed while building
and deploying the companion Temporal Drift Oracle primitive on
Bradbury:
- `DynArray` fields inside a nested `@allow_storage` dataclass must be
  allocated via `gl.storage.inmem_allocate(DynArray[ScoreEntry], [])`,
  not instantiated directly.
- LLM calls go through `gl.nondet.exec_prompt(...)`.

## Testing notes

Suggested manual walkthrough in GenLayer Studio:
1. Deploy with no constructor args.
2. `create_assessment("proposal1", "<subject description>", "<scoring criteria>")`.
3. `submit_score("proposal1")` — inspect `get_history` to confirm the
   recorded `tier` matches the `score` per the tier boundaries.
4. Run `submit_score` again on the same assessment to confirm
   `min_score` / `max_score` update correctly and `get_average_score`
   reflects both entries.

Edge cases worth testing explicitly:
- A subject worded ambiguously enough that the LLM's score could
  plausibly land near a tier boundary — confirm the equivalence
  principle correctly rejects validator disagreement when scores
  straddle a boundary (e.g. 38 vs 41).
- An assessment with only one `submit_score` call — `min_score` and
  `max_score` should be equal.
