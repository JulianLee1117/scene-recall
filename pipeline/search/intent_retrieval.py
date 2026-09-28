"""Opt-in evidence routing and controlled rank fusion over existing indexes.

There is no provider dependency here. Ordinary search remains the production
default. Comparisons share one pinned snapshot, baseline pool and query vectors;
they measure candidate generation rather than reranking a frozen shortlist.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import Callable, Iterable

from pipeline.index.text_features import resolve_ready_text_profile
from pipeline.search import retrieve
from pipeline.search.intent import ASPECTS, QueryIntent, intent_from_dict, normalise_request
from pipeline.search.request import search_execution


PLAN_VERSION = "bounded-evidence-routing-v1"
STRATEGIES = ("normal", "fixed", "jev")
EVIDENCE_VIEWS = ("caption", "facets", "mood", "dialogue", "ocr")
ASPECT_VIEW = {
    "visual_description": "caption", "appearance": "facets", "shot_type": "facets",
    "mood": "mood", "dialogue": "dialogue", "on_screen_text": "ocr",
}
MAX_TARGETED_ROUTES = 3
DEFAULT_SUPPLEMENTAL_BUDGET = 120
INTENT_THRESHOLD = .55
BASELINE_WEIGHT = .5
TARGETED_GROUP_WEIGHT = .5
RRF_K = 60


@dataclass(frozen=True)
class EvidenceRoute:
    view: str
    candidate_limit: int


@dataclass(frozen=True)
class RetrievalPlan:
    version: str
    strategy: str
    routes: tuple[EvidenceRoute, ...]
    supplemental_budget: int
    unsupported_aspects: tuple[str, ...]
    unavailable_views: tuple[str, ...]
    fallback_reason: str | None


@dataclass
class SearchExecution:
    results: list[dict]
    plan: RetrievalPlan
    diagnostics: dict


@dataclass
class ComparisonExecution:
    variants: dict[str, SearchExecution]
    diagnostics: dict


def _available_views(config, db) -> tuple[str, ...]:
    try:
        ready = resolve_ready_text_profile(config, db) is not None
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        ready = False
    return EVIDENCE_VIEWS if ready else ()


def build_plan(strategy: str, *, available_views: Iterable[str], intent: QueryIntent | None = None,
               supplemental_budget: int = DEFAULT_SUPPLEMENTAL_BUDGET) -> RetrievalPlan:
    """Compile known capabilities into bounded code-owned routes, never filters."""
    if strategy not in STRATEGIES:
        raise ValueError("Unknown retrieval strategy")
    if type(supplemental_budget) is not int or not 5 <= supplemental_budget <= 300:
        raise ValueError("Supplemental candidate budget must be an integer between 5 and 300")
    available = set(available_views)
    if not available.issubset(EVIDENCE_VIEWS):
        raise ValueError("Unknown evidence view")
    selected, unsupported, unavailable = [], [], []
    reason = None
    if strategy == "fixed":
        selected = [view for view in EVIDENCE_VIEWS if view in available]
    elif strategy == "jev":
        if intent is None:
            reason = "interpretation_unavailable"
        else:
            affirmative = [signal for signal in intent.signals
                           if signal.choice == "yes" and signal.probability >= INTENT_THRESHOLD]
            affirmative.sort(key=lambda signal: (-signal.probability, ASPECTS.index(signal.aspect)))
            for signal in affirmative:
                view = ASPECT_VIEW.get(signal.aspect)
                if view is None:
                    unsupported.append(signal.aspect)
                elif view not in available:
                    if view not in unavailable:
                        unavailable.append(view)
                elif view not in selected:
                    selected.append(view)
            selected = selected[:MAX_TARGETED_ROUTES]
            if not selected:
                reason = "no_available_supported_intent" if affirmative else "no_clear_supported_intent"
    if strategy == "fixed" and not selected:
        reason = "semantic_profile_unavailable"
    allocations = []
    for index, view in enumerate(selected):
        # Spend a fixed total across unique views, independent of route count.
        depth = supplemental_budget // len(selected) + (index < supplemental_budget % len(selected))
        allocations.append(EvidenceRoute(view, int(depth)))
    return RetrievalPlan(PLAN_VERSION, strategy, tuple(allocations), supplemental_budget,
                         tuple(unsupported), tuple(unavailable), reason)


def _scoped_unique(rows: Iterable[dict], film_ids: tuple[str, ...]) -> list[dict]:
    allowed, seen, selected = set(film_ids), set(), []
    for row in rows:
        identity = row.get("unit_id")
        if (not isinstance(identity, str) or not identity or identity in seen
                or (allowed and row.get("film_id") not in allowed)):
            continue
        seen.add(identity)
        selected.append(row)
    return selected


def fuse_rankings(baseline: Iterable[dict], targeted: dict[str, list[dict]], *,
                  film_ids: Iterable[str] = ()) -> list[dict]:
    """One baseline vote plus the strongest targeted vote, not one per route.

    Giving the targeted group its maximum rank vote keeps its influence fixed
    when routes overlap, duplicate one another, fail, or return no evidence.
    Original-query agreement earns both votes. Missing evidence is no veto.
    """
    scope = tuple(film_ids)
    ordinary = _scoped_unique(baseline, scope)
    targets = {view: _scoped_unique(rows, scope) for view, rows in targeted.items()}
    if not any(targets.values()):
        return deepcopy(ordinary)
    candidates = {}
    for rank, row in enumerate(ordinary, 1):
        candidates[row["unit_id"]] = {"row": row, "baseline_rank": rank, "routes": {}, "evidence": {}}
    for view in EVIDENCE_VIEWS:
        for rank, row in enumerate(targets.get(view, ()), 1):
            candidate = candidates.setdefault(row["unit_id"], {"row": row, "baseline_rank": None,
                                                               "routes": {}, "evidence": {}})
            candidate["routes"][view] = rank
            candidate["evidence"][view] = {
                "matched_text_view": row.get("matched_text_view"),
                "matched_text": row.get("matched_text"),
                "channels": deepcopy((row.get("debug") or {}).get("channels", {})),
            }
    ranked = []
    for identity, candidate in candidates.items():
        baseline_rank = candidate["baseline_rank"]
        best_rank = min(candidate["routes"].values(), default=None)
        base_vote = BASELINE_WEIGHT / (RRF_K + baseline_rank) if baseline_rank is not None else 0.
        targeted_vote = TARGETED_GROUP_WEIGHT / (RRF_K + best_rank) if best_rank is not None else 0.
        ranked.append((base_vote + targeted_vote, baseline_rank or float("inf"), identity, candidate,
                       base_vote, targeted_vote))
    ranked.sort(key=lambda value: (-value[0], value[1], value[2]))
    results = []
    for score, _, _, candidate, base_vote, targeted_vote in ranked:
        result = deepcopy(candidate["row"])
        debug = deepcopy(result.get("debug") or {})
        debug["final_score"] = score
        debug["intent_retrieval"] = {
            "policy": PLAN_VERSION,
            "baseline_rank": candidate["baseline_rank"],
            "targeted_ranks": candidate["routes"],
            "targeted_evidence": candidate["evidence"],
            "baseline_vote": base_vote,
            "targeted_group_vote": targeted_vote,
            "targeted_group_rule": "maximum_rank_vote",
        }
        result.update(rank=len(results) + 1, debug=debug)
        results.append(result)
    return results


def _capture(query, db, config, film_ids, plans, check_cancelled=None):
    if check_cancelled is not None:
        check_cancelled()
    started = perf_counter()
    baseline = retrieve.search(query, db, config, film_ids=film_ids, _return_candidate_pool=True)
    baseline = _scoped_unique(baseline, film_ids)
    baseline_seconds = perf_counter() - started
    depths = {}
    for plan in plans.values():
        for route in plan.routes:
            depths[route.view] = max(depths.get(route.view, 0), route.candidate_limit)
    targeted, fetches, failures = {}, {}, {}
    for view in EVIDENCE_VIEWS:
        depth = depths.get(view)
        if depth is None:
            continue
        if check_cancelled is not None:
            check_cancelled()
        route_config = replace(config, retrieval=replace(config.retrieval, candidate_limit=depth,
                               result_window=min(depth, config.retrieval.result_window), max_result_limit=depth))
        started = perf_counter()
        try:
            rows = retrieve.search_semantic_views(query, (view,), db, route_config, film_ids=film_ids,
                                                  result_limit=depth)
            targeted[view] = _scoped_unique(rows, film_ids)[:depth]
        except (retrieve.SemanticTextProfileUnavailable, RuntimeError, OSError, ValueError) as error:
            # A complete profile can still encounter a runtime model/index failure.
            # Record only its class; exception text may include paths or providers.
            targeted[view] = []
            failures[view] = type(error).__name__
        fetches[view] = {"requested_candidate_limit": depth,
                         "returned_candidate_rows": len(targeted[view]),
                         "seconds": perf_counter() - started}
    return baseline, targeted, {
        "baseline_adapter_calls": 1,
        "baseline_candidate_rows": len(baseline),
        "baseline_seconds": baseline_seconds,
        "supplemental_adapter_calls": len(fetches),
        "supplemental_fetches": fetches,
        "supplemental_returned_candidate_rows": sum(len(rows) for rows in targeted.values()),
        "route_failures": failures,
        "timing_scope": "Shared adapter calls, including encoding/hydration; not standalone strategy or production latency.",
        "count_scope": "Returned adapter candidate rows, not raw database rows scanned. Adapters internally oversample frames/views.",
    }


def _execute_captured(query, db, config, film_ids, result_limit, plan, baseline, targeted, failures):
    sliced = {route.view: targeted.get(route.view, [])[:route.candidate_limit] for route in plan.routes}
    combined = fuse_rankings(baseline, sliced, film_ids=film_ids)
    baseline_ids = {row["unit_id"] for row in baseline}
    before_preferences = {row["unit_id"] for row in combined}
    results = retrieve.apply_recipe_result_preferences(combined, db, config, film_ids=film_ids,
                                                       requested_text=query, result_limit=result_limit)
    # The same pinned scope is checked at both candidate and result boundaries.
    results = _scoped_unique(results, film_ids)
    unavailable = {view: failures[view] for view in sliced if view in failures}
    return SearchExecution(results, plan, {
        "baseline_candidate_rows": len(baseline),
        "logical_supplemental_limit": sum(route.candidate_limit for route in plan.routes),
        "logical_supplemental_rows": sum(len(rows) for rows in sliced.values()),
        "logical_route_limits": {route.view: route.candidate_limit for route in plan.routes},
        "candidate_union_rows": len(before_preferences),
        "additional_candidate_rows": len(before_preferences - baseline_ids),
        "additional_returned_rows": sum(row["unit_id"] not in baseline_ids for row in results),
        "route_failures": unavailable,
        "fallback_to_baseline": plan.strategy != "normal" and not any(sliced.values()),
        "quality_status": "unjudged; extra candidates and route confidence are not relevance or recall proof",
    })


def _validated_intent(intent, query, film_ids):
    if intent is None:
        return None
    if not isinstance(intent, QueryIntent):
        raise ValueError("Expected a validated QueryIntent")
    return intent_from_dict(asdict(intent), query=query, film_ids=film_ids)


@search_execution
def run_comparison(query: str, db, config, *, intent: QueryIntent | None = None,
                   film_ids: Iterable[str] = (), result_limit: int = 200,
                   supplemental_budget: int = DEFAULT_SUPPLEMENTAL_BUDGET,
                   check_cancelled: Callable[[], None] | None = None) -> ComparisonExecution:
    """Retrieve normal/fixed/Jev rankings from one snapshot and shared captures.

    Fixed and Jev allocate the same *additional* candidate-row budget when they
    select routes. Normal is the unchanged-cost control. Actual capture depths
    are the maximum needed per view across plans; they are reported separately.
    This deliberately does not claim equal compute or exhaustive library recall.
    """
    query, scope = normalise_request(query, film_ids)
    result_limit = retrieve.resolve_result_limit(config, result_limit)
    intent = _validated_intent(intent, query, scope)
    available = _available_views(config, db)
    plans = {strategy: build_plan(strategy, available_views=available, intent=intent,
                                 supplemental_budget=supplemental_budget) for strategy in STRATEGIES}
    baseline, targeted, diagnostics = _capture(query, db, config, scope, plans, check_cancelled)
    variants = {}
    for strategy, plan in plans.items():
        if check_cancelled is not None:
            check_cancelled()
        variants[strategy] = _execute_captured(query, db, config, scope, result_limit, plan,
                                              baseline, targeted, diagnostics["route_failures"])
    diagnostics.update({
        "contract": PLAN_VERSION,
        "query": query,
        "explicit_film_ids": list(scope),
        "available_views": list(available),
        "snapshot_versions": dict(getattr(db, "versions", {})),
        "intent": asdict(intent) if intent is not None else None,
        "budget_note": "Fixed/Jev share the supplemental row ceiling; normal spends none. Row budgets do not establish equal physical compute.",
        "fusion": {"rrf_k": RRF_K, "baseline_weight": BASELINE_WEIGHT,
                   "targeted_group_weight": TARGETED_GROUP_WEIGHT, "targeted_group_rule": "maximum_rank_vote"},
    })
    return ComparisonExecution(variants, diagnostics)


@search_execution
def execute_intent_search(query: str, db, config, *, strategy: str = "normal",
                          intent: QueryIntent | None = None, film_ids: Iterable[str] = (),
                          result_limit: int | None = None,
                          supplemental_budget: int = DEFAULT_SUPPLEMENTAL_BUDGET) -> SearchExecution:
    """Execute one explicit strategy, with no hosted calls or global activation."""
    query, scope = normalise_request(query, film_ids)
    result_limit = retrieve.resolve_result_limit(config, result_limit)
    intent = _validated_intent(intent, query, scope)
    available = _available_views(config, db) if strategy != "normal" else ()
    plan = build_plan(strategy, available_views=available, intent=intent, supplemental_budget=supplemental_budget)
    baseline, targeted, diagnostics = _capture(query, db, config, scope, {strategy: plan})
    result = _execute_captured(query, db, config, scope, result_limit, plan,
                               baseline, targeted, diagnostics["route_failures"])
    result.diagnostics["capture"] = diagnostics
    return result
