"""Subject backends: who or what is in the picture, for the measurement and moments passes.

Two backends produce one record (label, class code, score, box; a silhouette and keypoints
downstream), so every consumer (shot evidence, editor pools, match cuts, the Lab) reads the
same shape whichever ran:

* ``coco``: RF-DETR over COCO's 80 classes, with its own masks and RF-DETR keypoints. Fast,
  precise, rich in object classes; blind to drawn, stop-motion and non-human characters
  (Spirited Away: nobody found in 87% of shots that name a character).
* ``grounded``: Grounding DINO prompted with generic subject words (person, character, animal,
  creature, vehicle). Silhouettes come from the RF-DETR mask head's best-overlapping query, which
  localises drawn figures it cannot classify (box IoU 0.9 to 0.99 at class scores under 0.2);
  keypoints come from ViTPose on the person boxes (mean keypoint confidence 0.7 to 0.9 on drawn
  humanoids). About ten times the cost of ``coco`` per frame, so it is not the default.

A film's backend follows its Wikidata form and genre families (``ingest.grounded_subjects``,
Animation by default): drawn films get ``grounded`` everywhere, live action keeps ``coco``. The
backend is part of a grounded artifact's cache inputs and recorded in its data, so the two never
mix silently and a film re-describes when its backend changes; ``coco`` artifacts keep their
original inputs, so the library is not re-measured by this choice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

COCO = "coco"
GROUNDED = "grounded"
BACKENDS = (COCO, GROUNDED)
DEFAULT_GROUNDED_FAMILIES: tuple[str, ...] = ("Animation",)

# Prompt words, in the order the grounder is asked; a merged label ("character creature") takes the
# first word present. Class codes extend the dense COCO codes (1..80) the moments index stores.
LABELS: tuple[str, ...] = ("person", "character", "animal", "creature", "vehicle")
PROMPT = " . ".join(LABELS) + " ."
CODES = {"person": 1, "character": 1, "animal": 81, "creature": 82, "vehicle": 83}
NAMES = {"person": "person", "character": "person", "animal": "animal", "creature": "creature", "vehicle": "vehicle"}
EXTRA_CLASS_NAMES = {81: "animal", 82: "creature", 83: "vehicle"}

GROUNDER = "IDEA-Research/grounding-dino-tiny"
POSER = "usyd-community/vitpose-base-simple"
MODEL_NAMES = {"grounder": "grounding-dino-tiny", "masks": "rf-detr-seg-small-1.11-fp16 (best-overlap query)",
               "pose": "vitpose-base-simple"}
DETECT_THRESHOLD = 0.3
TEXT_THRESHOLD = 0.25
NMS_IOU = 0.7
MASK_MATCH_IOU = 0.5          # an RF-DETR query must overlap a grounded box this much to lend its mask
POSE_MIN_MEAN_CONFIDENCE = 0.4
POSE_MAX = 4
CHUNK = 8                     # images per grounder forward (activations, not weights, bound the memory)


@dataclass(frozen=True)
class Detection:
    label: str                # one of LABELS
    score: float
    box: np.ndarray           # x0, y0, x1, y1 as fractions of the image

    @property
    def code(self) -> int:
        return CODES[self.label]

    @property
    def name(self) -> str:
        return NAMES[self.label]


def label_of(text: str) -> str | None:
    """The prompt word a grounder label stands for: the first of LABELS it contains."""
    words = str(text).lower().split()
    for label in LABELS:
        if label in words:
            return label
    return None


def iou(p: np.ndarray, q: np.ndarray) -> float:
    inter = max(0.0, min(p[2], q[2]) - max(p[0], q[0])) * max(0.0, min(p[3], q[3]) - max(p[1], q[1]))
    union = (p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
    return float(inter / union) if union > 0 else 0.0


def nms(detections: Iterable[Detection], threshold: float = NMS_IOU) -> list[Detection]:
    """Strongest first; a box overlapping a kept one beyond ``threshold`` is the same subject
    found under another word (the grounder returns "person" and "character" for one figure)."""
    kept: list[Detection] = []
    for det in sorted(detections, key=lambda d: -d.score):
        if all(iou(det.box, other.box) < threshold for other in kept):
            kept.append(det)
    return kept


def backend_for_film(db: Any, film_id: str, families: Iterable[str] = DEFAULT_GROUNDED_FAMILIES) -> str:
    """``grounded`` when the film's genre families (its Wikidata genres and form) meet ``families``."""
    wanted = {str(f) for f in families}
    if not wanted:
        return COCO
    from pipeline.search.film_facets import film_facets
    facets = film_facets(db).get(film_id) or {}
    return GROUNDED if wanted & set(facets.get("genres") or []) else COCO


class GroundedSubjects:
    """Grounding DINO boxes and ViTPose keypoints, lazily loaded, batched on the device."""

    def __init__(self, device: Any = None):
        import torch
        self.torch = torch
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._grounder = None
        self._poser = None

    @property
    def grounder(self):
        if self._grounder is None:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
            processor = AutoProcessor.from_pretrained(GROUNDER)
            model = AutoModelForZeroShotObjectDetection.from_pretrained(GROUNDER).to(self.device).eval()
            self._grounder = (processor, model)
        return self._grounder

    @property
    def poser(self):
        if self._poser is None:
            from transformers import VitPoseForPoseEstimation, VitPoseImageProcessor
            processor = VitPoseImageProcessor.from_pretrained(POSER)
            model = VitPoseForPoseEstimation.from_pretrained(POSER).to(self.device).eval()
            self._poser = (processor, model)
        return self._poser

    def detect(self, images: list[np.ndarray]) -> list[list[Detection]]:
        """Per image (H x W x 3 uint8): subjects as fractions, strongest first, duplicates merged."""
        from PIL import Image
        torch = self.torch
        processor, model = self.grounder
        out: list[list[Detection]] = []
        for start in range(0, len(images), CHUNK):
            chunk = images[start:start + CHUNK]
            pil = [Image.fromarray(np.ascontiguousarray(image)) for image in chunk]
            inputs = processor(images=pil, text=[PROMPT] * len(pil), return_tensors="pt").to(self.device)
            with torch.inference_mode(), torch.autocast(self.device.type, dtype=torch.float16, enabled=self.device.type == "cuda"):
                outputs = model(**inputs)
            results = processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, threshold=DETECT_THRESHOLD, text_threshold=TEXT_THRESHOLD,
                target_sizes=[(image.shape[0], image.shape[1]) for image in chunk])
            for image, result in zip(chunk, results):
                height, width = image.shape[:2]
                labels = result.get("text_labels") or result.get("labels") or []
                found = []
                for box, score, text in zip(result["boxes"].tolist(), result["scores"].tolist(), labels):
                    label = label_of(text)
                    if label is None:
                        continue
                    frac = np.array([box[0] / width, box[1] / height, box[2] / width, box[3] / height], np.float32).clip(0, 1)
                    if frac[2] <= frac[0] or frac[3] <= frac[1]:
                        continue
                    found.append(Detection(label, float(score), frac))
                out.append(nms(found))
        return out

    def poses(self, images: list[np.ndarray], boxes: list[list[np.ndarray]]) -> list[list[tuple[float, np.ndarray, np.ndarray]]]:
        """Per image: ``(score, xy 17 x 2 fractions, confidence 17)`` for the largest people, from the
        given person boxes (fractions). Images without boxes get an empty list."""
        from PIL import Image
        torch = self.torch
        processor, model = self.poser
        out: list[list[tuple[float, np.ndarray, np.ndarray]]] = [[] for _ in images]
        wanted = [index for index, per in enumerate(boxes) if per]
        for start in range(0, len(wanted), CHUNK):
            chunk = wanted[start:start + CHUNK]
            pil, xywh = [], []
            for index in chunk:
                height, width = images[index].shape[:2]
                pil.append(Image.fromarray(np.ascontiguousarray(images[index])))
                xywh.append([[float(b[0] * width), float(b[1] * height), float((b[2] - b[0]) * width), float((b[3] - b[1]) * height)]
                             for b in boxes[index][:POSE_MAX]])
            inputs = processor(pil, boxes=xywh, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                outputs = model(**inputs)
            results = processor.post_process_pose_estimation(outputs, boxes=xywh)
            for index, per_image in zip(chunk, results):
                height, width = images[index].shape[:2]
                people = []
                for person in per_image:
                    xy = np.asarray(person["keypoints"], np.float32).reshape(-1, 2) / np.array([width, height], np.float32)
                    confidence = np.asarray(person["scores"], np.float32).reshape(-1)
                    mean = float(confidence.mean()) if len(confidence) else 0.0
                    if mean < POSE_MIN_MEAN_CONFIDENCE:
                        continue
                    seen = confidence >= 0.3
                    extent = (xy[seen].max(0) - xy[seen].min(0)) if seen.sum() >= 2 else np.zeros(2, np.float32)
                    people.append((float(extent[0] * extent[1] + extent[1] * 0.01), mean, xy.clip(0, 1), confidence))
                people.sort(key=lambda row: -row[0])
                out[index] = [(score, xy, confidence) for _rank, score, xy, confidence in people[:POSE_MAX]]
        return out


def match_masks(detections: list[Detection], query_boxes: np.ndarray) -> list[int]:
    """For each detection, the index of the query box overlapping it most (IoU at least
    MASK_MATCH_IOU), else -1: the RF-DETR query whose mask becomes the subject's silhouette."""
    picks = []
    for det in detections:
        if len(query_boxes) == 0:
            picks.append(-1)
            continue
        overlaps = np.array([iou(det.box, q) for q in query_boxes])
        best = int(overlaps.argmax())
        picks.append(best if overlaps[best] >= MASK_MATCH_IOU else -1)
    return picks


def configured_families(config: Any) -> tuple[str, ...]:
    """``ingest.grounded_subjects`` when the config carries it, else the default."""
    ingest = getattr(config, "ingest", None)
    families = getattr(ingest, "grounded_subjects", None)
    return tuple(families) if families is not None else DEFAULT_GROUNDED_FAMILIES


def cache_inputs(shots: str, backend: str) -> dict[str, str]:
    """A pass's cache inputs: the shots digest, plus the backend when it is not the default, so
    every existing ``coco`` artifact stays current and a grounded film re-describes on its own key."""
    inputs = {"shots": shots}
    if backend != COCO:
        inputs["subjects"] = backend
    return inputs


def record(backend: str) -> dict[str, Any]:
    """What an artifact says about its subjects."""
    if backend == GROUNDED:
        return {"backend": GROUNDED, "models": dict(MODEL_NAMES), "prompt": PROMPT}
    return {"backend": COCO}
