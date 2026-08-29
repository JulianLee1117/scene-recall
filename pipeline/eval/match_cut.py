"""Score reference-frame Match Cut retrieval against human-owned cases.

The case YAML contains durable frame references and sparse human judgments.
The ranked-results JSON is produced by whichever matcher is under test.  This
keeps evaluation independent of a matcher implementation and makes candidate
loss visible at each declared gate::

    python -m pipeline.eval.match_cut score \
      --cases pipeline/eval/match_cut_cases.yaml \
      --rankings pipeline/eval/runs/match-cut-legacy.json

The scorer never reads or combines vectors.  Each ranked-results file must
declare exact profile lineage, vector-space identities, and the ranking
contract used at every gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

import yaml


SCHEMA_VERSION = 1
CASE_KIND = "scene_recall_match_cut_cases"
RANKINGS_KIND = "scene_recall_match_cut_rankings"
REPORT_KIND = "scene_recall_match_cut_evaluation"
DEFAULT_CASES = Path(__file__).parent / "match_cut_cases.yaml"

CRITERIA = (
    "subject_object",
    "normalized_position",
    "scale",
    "viewpoint_orientation",
    "pose",
    "relations_negative_space",
)
LABELS = frozenset({"positive", "hard_negative"})
STRONG_GRADE = 2


@dataclass(frozen=True, order=True)
class FrameRef:
    """One exact indexed frame, used as a stable evaluation identity."""

    unit_id: str
    frame_index: int

    def as_document(self) -> dict[str, Any]:
        return {"unit_id": self.unit_id, "frame_index": self.frame_index}


@dataclass(frozen=True)
class Judgment:
    """Human-owned relevance label and optional per-criterion grades."""

    frame: FrameRef
    label: str
    criteria: Mapping[str, int | None]
    note: str


@dataclass(frozen=True)
class MatchCutCase:
    """One reference frame and its known positive/confusable candidates."""

    id: str
    description: str
    reference: FrameRef
    judgments: tuple[Judgment, ...]


@dataclass(frozen=True)
class Gate:
    """One explicitly described candidate or ranking boundary."""

    id: str
    description: str
    expected_depth: int
    profile_ids: tuple[str, ...]
    ranking_contract: str


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _nonempty_text(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be non-empty text")
    return value.strip()


def _positive_int(value: Any, *, context: str, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{context} must be a {qualifier} integer")
    return value


def _frame_ref(value: Any, *, context: str) -> FrameRef:
    raw = _mapping(value, context=context)
    unit_id = _nonempty_text(raw.get("unit_id"), context=f"{context}.unit_id")
    frame_index = _positive_int(
        raw.get("frame_index"),
        context=f"{context}.frame_index",
        allow_zero=True,
    )
    return FrameRef(unit_id=unit_id, frame_index=frame_index)


def load_case_document(document: Any) -> tuple[MatchCutCase, ...]:
    """Validate and normalize a version-1 human-authored case document."""
    raw_document = _mapping(document, context="case document")
    if raw_document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("case document must use schema_version 1")
    if raw_document.get("kind") != CASE_KIND:
        raise ValueError(f"case document kind must be {CASE_KIND!r}")

    raw_criteria = _mapping(
        raw_document.get("criteria"), context="case document criteria"
    )
    if set(raw_criteria) != set(CRITERIA):
        raise ValueError(
            "case criteria must contain exactly: " + ", ".join(CRITERIA)
        )
    for criterion in CRITERIA:
        _nonempty_text(
            raw_criteria[criterion], context=f"criterion {criterion!r} description"
        )

    raw_cases = raw_document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("case document must contain at least one case")

    cases: list[MatchCutCase] = []
    seen_case_ids: set[str] = set()
    for case_position, raw_case_value in enumerate(raw_cases, start=1):
        raw_case = _mapping(raw_case_value, context=f"case #{case_position}")
        case_id = _nonempty_text(
            raw_case.get("id"), context=f"case #{case_position}.id"
        )
        if case_id in seen_case_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_case_ids.add(case_id)
        description = _nonempty_text(
            raw_case.get("description"), context=f"case {case_id!r}.description"
        )
        reference = _frame_ref(
            raw_case.get("reference"), context=f"case {case_id!r}.reference"
        )

        raw_judgments = raw_case.get("judgments")
        if not isinstance(raw_judgments, list) or not raw_judgments:
            raise ValueError(f"case {case_id!r} must contain judgments")
        judgments: list[Judgment] = []
        seen_frames: set[FrameRef] = set()
        seen_labels: set[str] = set()
        for judgment_position, raw_judgment_value in enumerate(
            raw_judgments, start=1
        ):
            context = f"case {case_id!r} judgment #{judgment_position}"
            raw_judgment = _mapping(raw_judgment_value, context=context)
            frame = _frame_ref(raw_judgment, context=context)
            if frame == reference:
                raise ValueError(f"{context} cannot be the reference frame")
            if frame in seen_frames:
                raise ValueError(f"case {case_id!r} has duplicate judgment frame")
            seen_frames.add(frame)

            label = _nonempty_text(
                raw_judgment.get("label"), context=f"{context}.label"
            )
            if label not in LABELS:
                raise ValueError(
                    f"{context}.label must be positive or hard_negative"
                )
            seen_labels.add(label)

            raw_grades = _mapping(
                raw_judgment.get("criteria"), context=f"{context}.criteria"
            )
            if set(raw_grades) != set(CRITERIA):
                raise ValueError(
                    f"{context}.criteria must contain every declared criterion"
                )
            grades: dict[str, int | None] = {}
            for criterion in CRITERIA:
                grade = raw_grades[criterion]
                if grade is not None and (
                    isinstance(grade, bool)
                    or not isinstance(grade, int)
                    or grade not in range(4)
                ):
                    raise ValueError(
                        f"{context}.{criterion} must be null or an integer 0-3"
                    )
                grades[criterion] = grade

            note_value = raw_judgment.get("note", "")
            if not isinstance(note_value, str):
                raise ValueError(f"{context}.note must be text")
            judgments.append(
                Judgment(
                    frame=frame,
                    label=label,
                    criteria=grades,
                    note=note_value.strip(),
                )
            )

        missing_labels = LABELS - seen_labels
        if missing_labels:
            raise ValueError(
                f"case {case_id!r} needs at least one positive and hard_negative"
            )
        cases.append(
            MatchCutCase(
                id=case_id,
                description=description,
                reference=reference,
                judgments=tuple(judgments),
            )
        )
    return tuple(cases)


def load_cases(path: Path = DEFAULT_CASES) -> tuple[MatchCutCase, ...]:
    """Load a human-owned Match Cut case file from disk."""
    return load_case_document(_read_yaml(path))


def _validate_lineage(document: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("rankings must declare at least one profile")
    required = ("id", "model_id", "revision", "vector_space", "contract")
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw_profile_value in enumerate(raw_profiles, start=1):
        raw_profile = _mapping(raw_profile_value, context=f"profile #{position}")
        profile = {
            field: _nonempty_text(
                raw_profile.get(field), context=f"profile #{position}.{field}"
            )
            for field in required
        }
        if profile["id"] in seen:
            raise ValueError(f"duplicate profile id: {profile['id']}")
        seen.add(profile["id"])
        profiles.append(profile)
    return tuple(profiles)


def _validate_gates(
    document: Mapping[str, Any], profile_ids: set[str]
) -> tuple[Gate, ...]:
    raw_gates = document.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ValueError("rankings must declare at least one gate")
    gates: list[Gate] = []
    seen: set[str] = set()
    for position, raw_gate_value in enumerate(raw_gates, start=1):
        raw_gate = _mapping(raw_gate_value, context=f"gate #{position}")
        gate_id = _nonempty_text(raw_gate.get("id"), context=f"gate #{position}.id")
        if gate_id in seen:
            raise ValueError(f"duplicate gate id: {gate_id}")
        seen.add(gate_id)
        raw_gate_profiles = raw_gate.get("profile_ids")
        if (
            not isinstance(raw_gate_profiles, list)
            or not raw_gate_profiles
            or not all(isinstance(value, str) and value for value in raw_gate_profiles)
        ):
            raise ValueError(f"gate {gate_id!r}.profile_ids must be non-empty text")
        gate_profiles = tuple(raw_gate_profiles)
        if len(set(gate_profiles)) != len(gate_profiles):
            raise ValueError(f"gate {gate_id!r} repeats a profile")
        unknown = set(gate_profiles) - profile_ids
        if unknown:
            raise ValueError(
                f"gate {gate_id!r} references unknown profiles: {sorted(unknown)}"
            )
        gates.append(
            Gate(
                id=gate_id,
                description=_nonempty_text(
                    raw_gate.get("description"),
                    context=f"gate {gate_id!r}.description",
                ),
                expected_depth=_positive_int(
                    raw_gate.get("expected_depth"),
                    context=f"gate {gate_id!r}.expected_depth",
                ),
                profile_ids=gate_profiles,
                ranking_contract=_nonempty_text(
                    raw_gate.get("ranking_contract"),
                    context=f"gate {gate_id!r}.ranking_contract",
                ),
            )
        )
    return tuple(gates)


def _validate_rankings(
    document: Any,
    cases: Sequence[MatchCutCase],
) -> tuple[
    Mapping[str, Any],
    tuple[dict[str, Any], ...],
    tuple[Gate, ...],
    dict[str, dict[str, tuple[FrameRef, ...]]],
]:
    raw_document = _mapping(document, context="rankings document")
    if raw_document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("rankings document must use schema_version 1")
    if raw_document.get("kind") != RANKINGS_KIND:
        raise ValueError(f"rankings document kind must be {RANKINGS_KIND!r}")

    matcher = _mapping(raw_document.get("matcher"), context="matcher")
    matcher_identity = {
        "id": _nonempty_text(matcher.get("id"), context="matcher.id"),
        "revision": _nonempty_text(
            matcher.get("revision"), context="matcher.revision"
        ),
        "corpus_snapshot": _nonempty_text(
            matcher.get("corpus_snapshot"), context="matcher.corpus_snapshot"
        ),
    }
    profiles = _validate_lineage(raw_document)
    profile_ids = {profile["id"] for profile in profiles}
    gates = _validate_gates(raw_document, profile_ids)
    gate_ids = [gate.id for gate in gates]

    raw_case_runs = raw_document.get("cases")
    if not isinstance(raw_case_runs, list):
        raise ValueError("rankings cases must be a list")
    expected_case_ids = {case.id for case in cases}
    case_references = {case.id: case.reference for case in cases}
    rankings: dict[str, dict[str, tuple[FrameRef, ...]]] = {}
    for position, raw_case_run_value in enumerate(raw_case_runs, start=1):
        raw_case_run = _mapping(
            raw_case_run_value, context=f"rankings case #{position}"
        )
        case_id = _nonempty_text(
            raw_case_run.get("id"), context=f"rankings case #{position}.id"
        )
        if case_id in rankings:
            raise ValueError(f"duplicate rankings case id: {case_id}")
        raw_gate_rankings = _mapping(
            raw_case_run.get("rankings"),
            context=f"rankings case {case_id!r}.rankings",
        )
        if set(raw_gate_rankings) != set(gate_ids):
            raise ValueError(
                f"rankings case {case_id!r} must contain every declared gate"
            )
        normalized_gates: dict[str, tuple[FrameRef, ...]] = {}
        for gate in gates:
            raw_entries = raw_gate_rankings[gate.id]
            if not isinstance(raw_entries, list):
                raise ValueError(
                    f"rankings case {case_id!r} gate {gate.id!r} must be a list"
                )
            if len(raw_entries) > gate.expected_depth:
                raise ValueError(
                    f"rankings case {case_id!r} gate {gate.id!r} exceeds "
                    "expected_depth"
                )
            frames: list[FrameRef] = []
            seen_frames: set[FrameRef] = set()
            for rank, raw_entry_value in enumerate(raw_entries, start=1):
                context = (
                    f"rankings case {case_id!r} gate {gate.id!r} rank {rank}"
                )
                raw_entry = _mapping(raw_entry_value, context=context)
                entry_rank = _positive_int(
                    raw_entry.get("rank"), context=f"{context}.rank"
                )
                if entry_rank != rank:
                    raise ValueError(f"{context} must have sequential ranks")
                frame = _frame_ref(raw_entry, context=context)
                if frame in seen_frames:
                    raise ValueError(f"{context} repeats a ranked frame")
                if frame == case_references.get(case_id):
                    raise ValueError(f"{context} returns its own reference frame")
                seen_frames.add(frame)
                frames.append(frame)
            normalized_gates[gate.id] = tuple(frames)
        rankings[case_id] = normalized_gates

    actual_case_ids = set(rankings)
    if actual_case_ids != expected_case_ids:
        missing = sorted(expected_case_ids - actual_case_ids)
        extra = sorted(actual_case_ids - expected_case_ids)
        raise ValueError(
            "rankings cases must exactly match case set; "
            f"missing={missing}, extra={extra}"
        )
    return matcher_identity, profiles, gates, rankings


def _pairwise_accuracy(
    preferred: Sequence[FrameRef],
    disfavored: Sequence[FrameRef],
    ranks: Mapping[FrameRef, int],
) -> dict[str, int | float | None]:
    correct = 0
    comparisons = 0
    for preferred_frame in preferred:
        for disfavored_frame in disfavored:
            preferred_rank = ranks.get(preferred_frame)
            disfavored_rank = ranks.get(disfavored_frame)
            if preferred_rank is None and disfavored_rank is None:
                continue
            comparisons += 1
            if preferred_rank is not None and (
                disfavored_rank is None or preferred_rank < disfavored_rank
            ):
                correct += 1
    return {
        "correct": correct,
        "comparisons": comparisons,
        "accuracy": correct / comparisons if comparisons else None,
    }


def _hit_documents(
    frames: Sequence[FrameRef], ranks: Mapping[FrameRef, int]
) -> list[dict[str, Any]]:
    return [
        {**frame.as_document(), "rank": ranks[frame]}
        for frame in sorted(
            (frame for frame in frames if frame in ranks), key=ranks.__getitem__
        )
    ]


def _graded_pairwise_accuracy(
    judgments: Sequence[Judgment],
    criterion: str,
    ranks: Mapping[FrameRef, int],
) -> dict[str, int | float | None]:
    graded = [
        (judgment.frame, int(judgment.criteria[criterion]))
        for judgment in judgments
        if judgment.criteria[criterion] is not None
    ]
    correct = 0
    comparisons = 0
    for index, (left_frame, left_grade) in enumerate(graded):
        for right_frame, right_grade in graded[index + 1 :]:
            if left_grade == right_grade:
                continue
            preferred, disfavored = (
                (left_frame, right_frame)
                if left_grade > right_grade
                else (right_frame, left_frame)
            )
            pair = _pairwise_accuracy([preferred], [disfavored], ranks)
            correct += int(pair["correct"])
            comparisons += int(pair["comparisons"])
    return {
        "correct": correct,
        "comparisons": comparisons,
        "accuracy": correct / comparisons if comparisons else None,
    }


def _criterion_metrics(
    judgments: Sequence[Judgment],
    criterion: str,
    ranks: Mapping[FrameRef, int],
) -> dict[str, Any]:
    graded = [
        judgment for judgment in judgments if judgment.criteria[criterion] is not None
    ]
    strong = [
        judgment.frame
        for judgment in graded
        if int(judgment.criteria[criterion]) >= STRONG_GRADE
    ]
    weak = [
        judgment.frame
        for judgment in graded
        if int(judgment.criteria[criterion]) < STRONG_GRADE
    ]
    hits = _hit_documents(strong, ranks)
    weak_hits = _hit_documents(weak, ranks)
    retrieved_grades = [
        {
            **judgment.frame.as_document(),
            "rank": ranks[judgment.frame],
            "grade": judgment.criteria[criterion],
        }
        for judgment in sorted(
            (judgment for judgment in graded if judgment.frame in ranks),
            key=lambda judgment: ranks[judgment.frame],
        )
    ]
    return {
        "graded_judgments": len(graded),
        "retrieved_grades": retrieved_grades,
        "mean_retrieved_grade": (
            fmean(float(row["grade"]) for row in retrieved_grades)
            if retrieved_grades
            else None
        ),
        "strong_matches": len(strong),
        "strong_match_hits": hits,
        "strong_match_recall": len(hits) / len(strong) if strong else None,
        "first_strong_match_rank": hits[0]["rank"] if hits else None,
        "weak_or_mismatch_judgments": len(weak),
        "weak_or_mismatch_hits": weak_hits,
        "pairwise_ranking": _graded_pairwise_accuracy(
            judgments, criterion, ranks
        ),
    }


def _score_case_gate(
    case: MatchCutCase, gate: Gate, ranked_frames: Sequence[FrameRef]
) -> dict[str, Any]:
    ranks = {frame: rank for rank, frame in enumerate(ranked_frames, start=1)}
    positives = [
        judgment.frame for judgment in case.judgments if judgment.label == "positive"
    ]
    hard_negatives = [
        judgment.frame
        for judgment in case.judgments
        if judgment.label == "hard_negative"
    ]
    positive_hits = _hit_documents(positives, ranks)
    hard_negative_hits = _hit_documents(hard_negatives, ranks)
    return {
        "depth": len(ranked_frames),
        "expected_depth": gate.expected_depth,
        "fill_rate": len(ranked_frames) / gate.expected_depth,
        "known_positives": len(positives),
        "known_positive_hits": positive_hits,
        "known_positive_recall": len(positive_hits) / len(positives),
        "first_known_positive_rank": (
            positive_hits[0]["rank"] if positive_hits else None
        ),
        "known_hard_negatives": len(hard_negatives),
        "known_hard_negative_hits": hard_negative_hits,
        "known_hard_negative_retrieval_rate": (
            len(hard_negative_hits) / len(hard_negatives)
        ),
        "positive_before_hard_negative": _pairwise_accuracy(
            positives, hard_negatives, ranks
        ),
        "criteria": {
            criterion: _criterion_metrics(case.judgments, criterion, ranks)
            for criterion in CRITERIA
        },
    }


def _aggregate_gate(
    gate: Gate, case_gate_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    positive_total = sum(int(row["known_positives"]) for row in case_gate_rows)
    positive_hits = sum(
        len(row["known_positive_hits"]) for row in case_gate_rows
    )
    hard_negative_total = sum(
        int(row["known_hard_negatives"]) for row in case_gate_rows
    )
    hard_negative_hits = sum(
        len(row["known_hard_negative_hits"]) for row in case_gate_rows
    )
    first_ranks = [
        int(row["first_known_positive_rank"])
        for row in case_gate_rows
        if row["first_known_positive_rank"] is not None
    ]
    pair_correct = sum(
        int(row["positive_before_hard_negative"]["correct"])
        for row in case_gate_rows
    )
    pair_total = sum(
        int(row["positive_before_hard_negative"]["comparisons"])
        for row in case_gate_rows
    )

    criterion_report: dict[str, Any] = {}
    for criterion in CRITERIA:
        rows = [row["criteria"][criterion] for row in case_gate_rows]
        strong_total = sum(int(row["strong_matches"]) for row in rows)
        strong_hits = sum(len(row["strong_match_hits"]) for row in rows)
        criterion_pairs = sum(
            int(row["pairwise_ranking"]["comparisons"]) for row in rows
        )
        criterion_correct = sum(
            int(row["pairwise_ranking"]["correct"]) for row in rows
        )
        criterion_report[criterion] = {
            "graded_cases": sum(int(row["graded_judgments"]) > 0 for row in rows),
            "graded_judgments": sum(int(row["graded_judgments"]) for row in rows),
            "strong_matches": strong_total,
            "strong_match_hits": strong_hits,
            "strong_match_recall_micro": (
                strong_hits / strong_total if strong_total else None
            ),
            "pairwise_ranking": {
                "correct": criterion_correct,
                "comparisons": criterion_pairs,
                "accuracy": (
                    criterion_correct / criterion_pairs if criterion_pairs else None
                ),
            },
        }

    return {
        "description": gate.description,
        "expected_depth": gate.expected_depth,
        "profile_ids": list(gate.profile_ids),
        "ranking_contract": gate.ranking_contract,
        "mean_depth": fmean(float(row["depth"]) for row in case_gate_rows),
        "mean_fill_rate": fmean(float(row["fill_rate"]) for row in case_gate_rows),
        "known_positive_recall_micro": positive_hits / positive_total,
        "known_positive_recall_macro": fmean(
            float(row["known_positive_recall"]) for row in case_gate_rows
        ),
        "known_positive_success_rate": sum(
            bool(row["known_positive_hits"]) for row in case_gate_rows
        )
        / len(case_gate_rows),
        "mean_first_known_positive_rank_on_success": (
            fmean(first_ranks) if first_ranks else None
        ),
        "known_hard_negative_retrieval_rate_micro": (
            hard_negative_hits / hard_negative_total
        ),
        "positive_before_hard_negative": {
            "correct": pair_correct,
            "comparisons": pair_total,
            "accuracy": pair_correct / pair_total if pair_total else None,
        },
        "criteria": criterion_report,
    }


def _case_transitions(
    case: MatchCutCase,
    gates: Sequence[Gate],
    rankings: Mapping[str, Sequence[FrameRef]],
) -> list[dict[str, Any]]:
    positives = {
        judgment.frame for judgment in case.judgments if judgment.label == "positive"
    }
    transitions: list[dict[str, Any]] = []
    for earlier, later in zip(gates, gates[1:], strict=False):
        earlier_hits = positives.intersection(rankings[earlier.id])
        later_hits = positives.intersection(rankings[later.id])
        transitions.append(
            {
                "from": earlier.id,
                "to": later.id,
                "known_positives_lost": [
                    frame.as_document() for frame in sorted(earlier_hits - later_hits)
                ],
                "known_positives_gained": [
                    frame.as_document() for frame in sorted(later_hits - earlier_hits)
                ],
            }
        )
    return transitions


def score_match_cut(case_document: Any, rankings_document: Any) -> dict[str, Any]:
    """Validate both inputs and score known judgments at every declared gate."""
    cases = load_case_document(case_document)
    matcher, profiles, gates, rankings = _validate_rankings(
        rankings_document, cases
    )

    case_rows: list[dict[str, Any]] = []
    rows_by_gate: dict[str, list[Mapping[str, Any]]] = {
        gate.id: [] for gate in gates
    }
    for case in cases:
        gate_rows = {
            gate.id: _score_case_gate(case, gate, rankings[case.id][gate.id])
            for gate in gates
        }
        for gate_id, row in gate_rows.items():
            rows_by_gate[gate_id].append(row)
        case_rows.append(
            {
                "id": case.id,
                "description": case.description,
                "reference": case.reference.as_document(),
                "gates": gate_rows,
                "transitions": _case_transitions(
                    case, gates, rankings[case.id]
                ),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "inputs": {
            "case_set_sha256": _canonical_sha256(case_document),
            "rankings_sha256": _canonical_sha256(rankings_document),
        },
        "matcher": dict(matcher),
        "profiles": list(profiles),
        "cases_evaluated": len(cases),
        "gates": {
            gate.id: _aggregate_gate(gate, rows_by_gate[gate.id])
            for gate in gates
        },
        "cases": case_rows,
        "metric_note": (
            "Known-positive recall covers only the explicit human judgments, "
            "not all relevant frames in the corpus. Unjudged ranked candidates "
            "are neither positives nor negatives. Null criterion grades are "
            "omitted rather than treated as zero. The scorer consumes ranks "
            "only and never combines vector scores or vector spaces."
        ),
    }


def _score_command(args: argparse.Namespace) -> None:
    case_document = _read_yaml(args.cases)
    rankings_document = _read_json(args.rankings)
    report = score_match_cut(case_document, rankings_document)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
        return
    if args.output.exists():
        raise FileExistsError(f"score output already exists: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote Match Cut score report to {args.output}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score reference-frame Match Cut retrieval gates."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    score = commands.add_parser(
        "score", help="score a matcher-produced ranked-results JSON"
    )
    score.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    score.add_argument("--rankings", type=Path, required=True)
    score.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        _score_command(args)
    except (
        FileExistsError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
