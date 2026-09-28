"""Compare the arrangement of already selected people at two source frames.

These development thresholds describe screen geometry, not editorial confidence.
Every person and every inter-person spacing must pass independently. Full-source
coordinates remain unchanged. Picture aspect restores diagnostic box aspect;
arm pose and outline width are not requirements for matching an arrangement.
The detector owns person selection, so this scorer never drops a missing partner
or selects a convenient subset of a larger candidate group.
"""
from __future__ import annotations

from itertools import combinations, permutations
from math import hypot, isfinite
from numbers import Real


MAX_PEOPLE = 3
MAX_CENTER_DISTANCE = .15  # Euclidean distance in normalized source coordinates.
MIN_HEIGHT_RATIO = .60  # Symmetric smaller/larger on-screen extent ratios.
MIN_AREA_RATIO = .25  # Reject gross mask-size differences, not changing arm pose.
MAX_SPACING_VECTOR_DIFFERENCE = .15
MIN_SPACING_RATIO = .50
_EPSILON = 1e-7


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)


def _valid(person):
    if not isinstance(person, dict) or person.get("visible", True) is not True:
        return False
    center, box = person.get("centroid"), person.get("box")
    if not isinstance(center, (list, tuple)) or len(center) != 2 or not isinstance(box, dict):
        return False
    values = [*center, *(box.get(key) for key in ("x", "y", "width", "height")),
              person.get("area"), person.get("picture_aspect")]
    if not all(_number(value) for value in values):
        return False
    x, y, width, height = (box[key] for key in ("x", "y", "width", "height"))
    return (all(0 <= value <= 1 for value in center)
            and 0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
            and x + width <= 1 + _EPSILON and y + height <= 1 + _EPSILON
            and x - _EPSILON <= center[0] <= x + width + _EPSILON
            and y - _EPSILON <= center[1] <= y + height + _EPSILON
            and 0 < person["area"] <= width * height + _EPSILON
            and person["picture_aspect"] > 0)


def _ratio(first, second):
    return min(first, second) / max(first, second) if max(first, second) else 1.


def _extent_quality(ratio, minimum):
    return max(0., min(1., (ratio - minimum) / (1. - minimum)))


def _assignment(reference, candidate, order):
    people, spacing, qualities, violations = [], [], [], []
    for index, candidate_index in enumerate(order):
        first, second = reference[index], candidate[candidate_index]
        dx, dy = (second["centroid"][axis] - first["centroid"][axis] for axis in (0, 1))
        distance = hypot(dx, dy)
        height_ratio = second["box"]["height"] / first["box"]["height"]
        area_ratio = second["area"] / first["area"]
        aspects = [person["box"]["width"] / person["box"]["height"] * person["picture_aspect"]
                   for person in (first, second)]
        height_similarity = _ratio(first["box"]["height"], second["box"]["height"])
        area_similarity = _ratio(first["area"], second["area"])
        aspect_similarity = _ratio(*aspects)
        passed = (distance <= MAX_CENTER_DISTANCE + _EPSILON
                  and height_similarity >= MIN_HEIGHT_RATIO - _EPSILON
                  and area_similarity >= MIN_AREA_RATIO - _EPSILON)
        if not passed:
            violations.append(f"person_{index}")
        # Layout is center and height, with area only a gross-size guard.
        # Width, area and physical aspect change with arm pose: those measured
        # diagnostics must not silently turn layout into an outline requirement.
        # One correctly matched person cannot compensate for a missing partner.
        quality = min(max(0., 1. - distance / MAX_CENTER_DISTANCE),
                      _extent_quality(height_similarity, MIN_HEIGHT_RATIO))
        qualities.append(quality)
        people.append({"reference_index": index, "candidate_index": candidate_index,
                       "reference_center": list(first["centroid"]), "candidate_center": list(second["centroid"]),
                       "horizontal_difference": dx, "vertical_difference": dy, "center_distance": distance,
                       "candidate_height_ratio": height_ratio, "candidate_area_ratio": area_ratio,
                       "height_similarity": height_similarity, "area_similarity": area_similarity,
                       "reference_physical_aspect": aspects[0], "candidate_physical_aspect": aspects[1],
                       "physical_aspect_similarity": aspect_similarity, "strength": quality, "reliable": passed})

    for left, right in combinations(range(len(reference)), 2):
        first = [reference[right]["centroid"][axis] - reference[left]["centroid"][axis] for axis in (0, 1)]
        second = [candidate[order[right]]["centroid"][axis] - candidate[order[left]]["centroid"][axis] for axis in (0, 1)]
        first_distance, second_distance = hypot(*first), hypot(*second)
        vector_difference = hypot(*(second[axis] - first[axis] for axis in (0, 1)))
        ratio = _ratio(first_distance, second_distance)
        passed = (vector_difference <= MAX_SPACING_VECTOR_DIFFERENCE + _EPSILON
                  and ratio >= MIN_SPACING_RATIO - _EPSILON)
        if not passed:
            violations.append(f"spacing_{left}_{right}")
        quality = min(max(0., 1. - vector_difference / MAX_SPACING_VECTOR_DIFFERENCE),
                      _extent_quality(ratio, MIN_SPACING_RATIO))
        qualities.append(quality)
        spacing.append({"reference_indices": [left, right], "candidate_indices": [order[left], order[right]],
                        "reference_vector": first, "candidate_vector": second,
                        "reference_distance": first_distance, "candidate_distance": second_distance,
                        "vector_difference": vector_difference, "distance_similarity": ratio,
                        "strength": quality, "reliable": passed})
    return {"reliable": not violations, "strength": min(qualities),
            "measurements": {"reference_people": len(reference), "candidate_people": len(candidate),
                             "assignment": people, "spacing": spacing, "failed_checks": violations}}


def compare_people(reference, candidate):
    """Return a conservative arrangement match for two complete salient groups.

    Each group is a list of 1–3 full-source-normalized person mask summaries.
    Assignment handles detector ordering only; coordinates are never reflected,
    translated or resized. The output establishes no identity, pose or movement.
    """
    counts = {"reference_people": len(reference) if isinstance(reference, (list, tuple)) else None,
              "candidate_people": len(candidate) if isinstance(candidate, (list, tuple)) else None}
    rejected = {"reliable": False, "strength": 0., "measurements": {**counts, "assignment": [], "spacing": []}}
    if not all(isinstance(group, (list, tuple)) and 1 <= len(group) <= MAX_PEOPLE for group in (reference, candidate)):
        return {**rejected, "reason": "unsupported_person_count"}
    if len(reference) != len(candidate):
        return {**rejected, "reason": "person_count_mismatch"}
    if not all(_valid(person) for group in (reference, candidate) for person in group):
        return {**rejected, "reason": "invalid_person_geometry"}
    comparisons = [_assignment(reference, candidate, order) for order in permutations(range(len(candidate)))]
    # Prefer a valid assignment, then its weakest geometric agreement. Detector
    # order only breaks exact ties; no person's identity is inferred.
    best = max(comparisons, key=lambda result: (result["reliable"], result["strength"],
               -sum(person["center_distance"] for person in result["measurements"]["assignment"])))
    if not best["reliable"]:
        return {**best, "strength": 0., "reason": "arrangement_mismatch"}
    return best
