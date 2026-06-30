# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Graduated Confidence Consensus
================================

A reusable GenLayer Intelligent Contract primitive for assessments
that are inherently a SPECTRUM, not a binary fact - quality scores,
risk ratings, credit-style assessments, code review scores, content
moderation severity, and similar judgements where forcing validators
to output byte-identical numbers would be both unrealistic and a poor
model of what "agreement" actually means for this kind of question.

Why this is a different consensus shape
----------------------------------------
The naive way to get validators to agree on a numeric score is strict
equality - but two independent LLM evaluations of "how risky is this
proposal, 0-100" landing on 61 and 64 are not in disagreement, they are
in agreement with normal evaluative noise. A contract that rejects
that as non-equivalent either becomes unusable (constant
non-finalization) or pushes builders toward trivial yes/no questions
instead of the graded judgements that are actually useful on-chain.

This primitive treats numeric scores as inherently fuzzy and uses a
custom equivalence principle that:
  - requires validators to agree on the discrete TIER the score falls
    into (e.g. low / medium / high risk) - this is the part that must
    be strict, because tier is what downstream logic acts on
  - tolerates raw numeric scores diverging within a configurable band
    around each other - this is the part that is allowed to be fuzzy,
    because the exact number is informational, not action-triggering
  - stores the band (min/max/average of agreeing validator scores)
    rather than collapsing to a single misleadingly-precise number,
    so downstream contracts can read both the tier and the actual
    spread of evaluative opinion

This makes the contract a genuine primitive for any use case that
needs a trustworthy graded score rather than a one-off "AI decides X"
demo: grant proposal risk scoring, code review quality gates,
freelance work quality scoring for escrow release, content severity
scoring for moderation queues, and more.

Consensus mechanism
--------------------
For every `submit_score` call:
  1. A non-deterministic block prompts the LLM with the assessment
     subject and scoring criteria, asking for a numeric score (0-100)
     and a short justification.
  2. `gl.eq_principle.prompt_comparative` is used with a principle that
     defines tier boundaries explicitly and tells validators exactly
     what must match (the tier the score falls into) and what is
     allowed to vary (the raw numeric score, within a configurable
     tolerance band, and the justification wording).
  3. On consensus, the contract stores the agreed tier alongside the
     leader's numeric score, and tracks the running min/max range
     across this assessment's score history so downstream readers can
     see both the categorical judgement and how much evaluative spread
     exists around it.
"""

from genlayer import *
from dataclasses import dataclass
import json

# ---------------------------------------------------------------------
# Tier configuration - the strict part of the equivalence principle
# ---------------------------------------------------------------------

TIER_BOUNDARIES = (
    ("low", 0, 39),
    ("medium", 40, 69),
    ("high", 70, 100),
)
SCORE_TOLERANCE = 12  # points validators' raw scores may diverge by


def tier_for_score(score: int) -> str:
    for name, lo, hi in TIER_BOUNDARIES:
        if lo <= score <= hi:
            return name
    return "unknown"


def tier_description() -> str:
    return ", ".join(f"{name} ({lo}-{hi})" for name, lo, hi in TIER_BOUNDARIES)


# ---------------------------------------------------------------------
# Storage schema
# ---------------------------------------------------------------------

@allow_storage
@dataclass
class ScoreEntry:
    epoch: u256
    score: u256
    tier: str
    justification: str


@allow_storage
@dataclass
class Assessment:
    subject: str
    criteria: str
    owner: Address
    score_count: u256
    min_score: u256
    max_score: u256
    last_tier: str
    history: DynArray[ScoreEntry]


class GraduatedConfidenceConsensus(gl.Contract):
    assessments: TreeMap[str, Assessment]

    def __init__(self):
        self.assessments = TreeMap()

    # -------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------

    @gl.public.write
    def create_assessment(self, assessment_id: str, subject: str, criteria: str) -> None:
        """Register a new subject to be scored. `subject` describes what
        is being evaluated, `criteria` describes what a high vs low
        score means for this specific assessment."""
        if assessment_id in self.assessments:
            raise Exception("assessment_id already exists")
        if len(subject.strip()) == 0:
            raise Exception("subject cannot be empty")
        if len(criteria.strip()) == 0:
            raise Exception("criteria cannot be empty")

        self.assessments[assessment_id] = Assessment(
            subject=subject,
            criteria=criteria,
            owner=gl.message.sender_address,
            score_count=u256(0),
            min_score=u256(0),
            max_score=u256(0),
            last_tier="unscored",
            history=gl.storage.inmem_allocate(DynArray[ScoreEntry], []),
        )

    @gl.public.write
    def submit_score(self, assessment_id: str) -> None:
        """Run a graded LLM evaluation and reach validator consensus on
        the score's tier, with raw numeric divergence tolerated within
        SCORE_TOLERANCE points."""
        if assessment_id not in self.assessments:
            raise Exception("unknown assessment_id")

        assessment = self.assessments[assessment_id]
        subject = assessment.subject
        criteria = assessment.criteria
        tiers = tier_description()

        def grade() -> str:
            prompt = f"""
You are evaluating the following subject on a 0-100 scale.

Subject: {subject}
Scoring criteria: {criteria}

Score bands for reference: {tiers}

Respond with ONLY a compact JSON object, no markdown, no commentary:
{{"score": <integer 0-100>, "justification": "<one short sentence>"}}
"""
            result_text = gl.nondet.exec_prompt(prompt)
            cleaned = result_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            parsed = json.loads(cleaned)

            score = int(parsed.get("score", 0))
            score = max(0, min(100, score))
            justification = str(parsed.get("justification", ""))[:280]
            tier = tier_for_score(score)

            return json.dumps({"score": score, "tier": tier, "justification": justification})

        principle = f"""
Two answers are EQUIVALENT only if the "tier" field is exactly the
same value in both answers. Tier boundaries are: {tiers}.

The "score" field may differ between the two answers by up to
{SCORE_TOLERANCE} points and still be considered equivalent, as long
as both scores fall within the same tier. If the two scores fall into
different tiers, the answers are NOT equivalent, even if the numeric
difference is small (e.g. 39 vs 40 are different tiers and must be
rejected as non-equivalent). The "justification" field may be worded
differently as long as it does not contradict the agreed tier.
"""

        consensus_result = gl.eq_principle.prompt_comparative(grade, principle)
        parsed = json.loads(consensus_result)

        score = u256(parsed["score"])
        tier = parsed["tier"]
        justification = parsed["justification"]

        next_epoch = u256(int(assessment.score_count) + 1)

        if int(assessment.score_count) == 0:
            assessment.min_score = score
            assessment.max_score = score
        else:
            if int(score) < int(assessment.min_score):
                assessment.min_score = score
            if int(score) > int(assessment.max_score):
                assessment.max_score = score

        assessment.history.append(
            ScoreEntry(
                epoch=next_epoch,
                score=score,
                tier=tier,
                justification=justification,
            )
        )
        assessment.score_count = next_epoch
        assessment.last_tier = tier

    # -------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------

    @gl.public.view
    def get_current_assessment(self, assessment_id: str) -> dict:
        a = self.assessments.get(assessment_id, None)
        if a is None:
            raise Exception("unknown assessment_id")
        return {
            "tier": a.last_tier,
            "score_count": int(a.score_count),
            "min_score": int(a.min_score),
            "max_score": int(a.max_score),
        }

    @gl.public.view
    def get_history(self, assessment_id: str) -> list:
        a = self.assessments.get(assessment_id, None)
        if a is None:
            raise Exception("unknown assessment_id")
        return [
            {
                "epoch": int(e.epoch),
                "score": int(e.score),
                "tier": e.tier,
                "justification": e.justification,
            }
            for e in a.history
        ]

    @gl.public.view
    def get_average_score(self, assessment_id: str) -> int:
        a = self.assessments.get(assessment_id, None)
        if a is None:
            raise Exception("unknown assessment_id")
        if int(a.score_count) == 0:
            return 0
        total = 0
        for e in a.history:
            total += int(e.score)
        return total // int(a.score_count)

    @gl.public.view
    def list_assessment_ids(self) -> list:
        return list(self.assessments.keys())
