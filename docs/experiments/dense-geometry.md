# Independent DINOv3 dense geometry challenger

This bounded offline adapter implements the admitted class-agnostic comparison
experiment. It is not active in search or the Lab. No checkpoint has been
downloaded or tested on this library as part of implementation; its real visual
quality, latency, memory use, and usefulness remain unmeasured.

The adapter follows the built-in [Transformers DINOv3 interface](https://huggingface.co/docs/transformers/model_doc/dinov3)
and its documented split of CLS, register, and spatial patch tokens. Consult
the [DINOv3 reference implementation](https://github.com/facebookresearch/dinov3)
for model provenance and licensed checkpoint access. This CLI never accesses
the Hub, requests access, downloads weights, or executes repository code.

## Prepare a concrete local profile

Supply an existing Hugging Face **DINOv3ViT** checkpoint directory containing
`config.json`, `preprocessor_config.json`, and `model.safetensors` or a complete
`model.safetensors.index.json` with local shard basenames. Pickle weights,
custom-code mappings, quantized checkpoints, ambiguous single/sharded weights,
and other model families are rejected.

```powershell
uv run python -m pipeline.experiments.dense_geometry profile --checkpoint C:/models/dinov3-vits16 --model-id facebook/dinov3-vits16-pretrain-lvd1689m --image-size 448 --output pipeline/eval/runs/dense-v1/profile.json
```

Profile creation only hashes local files. The versioned profile pins weight,
configuration, and preprocessor content, descriptor dimensions, prefix token
count, preprocessing, comparison contract, and installed Transformers, PyTorch,
torchvision, Pillow, and NumPy versions. Its content digest determines the
profile ID. Extraction checks the entire profile again before loading.
Changing weights, preprocessing, input resolution, or runtime requires a new
profile and an independent descriptor bundle.

## Extract a small explicit image set

Prepare an operator-owned JSON file. Paths may be absolute or relative to this
JSON file; they are **CLI inputs**, never browser-supplied media paths. Use exact
indexed frame identities where available, or durable operator image IDs.
Preserve film/unit/time evidence in `source` when it is known. The adapter hashes
the actual image bytes and does not infer source provenance from the filename.

```json
{
  "schema_version": 1,
  "kind": "dense_geometry_images",
  "frames": [
    {"id": "reference-unit:0", "path": "reference.webp", "source": {"unit_id": "reference-unit", "frame_index": 0}},
    {"id": "candidate-unit:1", "path": "candidate.webp", "source": {"unit_id": "candidate-unit", "frame_index": 1}}
  ]
}
```

```powershell
uv run python -m pipeline.experiments.dense_geometry extract --checkpoint C:/models/dinov3-vits16 --profile pipeline/eval/runs/dense-v1/profile.json --images pipeline/eval/runs/dense-v1/images.json --output pipeline/eval/runs/dense-v1/bundle
```

CPU is the default. Explicit `--device cuda` acquires the existing global ingest
resource lock for checkpoint loading and extraction. All model/processor loads
use built-in classes, `local_files_only=True`, `trust_remote_code=False`, and
safetensors weights. Inference uses float32, eager attention, and evaluation
mode. Nothing is installed or downloaded automatically.

Each source image is converted to RGB, resized with preserved aspect ratio,
and centered on a black square. The profile records this transformation and
the resulting source viewport. The checkpoint processor resizes or crops
nothing further; its pinned rescaling and normalization still apply. Rotated
EXIF images must be normalized explicitly before regions are supplied.

Only spatial tokens from the last hidden state are stored: CLS and register
tokens are removed, each patch is L2 normalized, and shape is checked against
the profile. NPZ files never contain pickle objects. A completed manifest
records every image and descriptor hash, source evidence, viewport, expected
and completed counts, and a coverage digest. Failed extraction leaves an
incomplete directory with no completed manifest, which ranking rejects.

The run is limited to **200 images**, each at most 64 MiB and 20 million pixels.
Input resolution is 224–672 pixels and must divide evenly into model patches.
All outputs must be new paths, preserving old runs and profiles.

## Compare full images or selected regions

Regions use `{x, y, width, height}` normalized to the original operator image,
including any source letterboxing. They do not use active-picture layout
coordinates. Omit `region` for the entire image.

```json
{
  "schema_version": 1,
  "kind": "dense_geometry_queries",
  "cases": [{
    "id": "circle-rhyme-diagnostic",
    "reference": {"frame_id": "reference-unit:0", "region": {"x": 0.3, "y": 0.3, "width": 0.4, "height": 0.4}},
    "candidates": [{"frame_id": "candidate-unit:1", "region": {"x": 0.4, "y": 0.4, "width": 0.2, "height": 0.2}}]
  }]
}
```

```powershell
uv run python -m pipeline.experiments.dense_geometry rank --bundle pipeline/eval/runs/dense-v1/bundle --queries pipeline/eval/runs/dense-v1/queries.json --output pipeline/eval/runs/dense-v1/ranking.json
```

The comparer maps each region through its stored viewport, bilinearly samples
an 8×8 grid of patch features, renormalizes each cell, and averages cosine
similarity at corresponding cells. This compares spatial feature arrangements
inside regions even when those regions occupy different source positions or
sizes. Equal scores break ties by stable identity. Each run permits at most
200 query cases and 5,000 candidate comparisons within one 200-frame bundle.

These are independent DINOv3 rankings, not mixed PE/vector scores. They do not
require matching object classes. They also **do not establish shape-only,
rotation, crop, or scale invariance**: appearance, semantics, source context,
padding, and preprocessing can influence the features. A tiny region may have
too few native patch samples to provide detailed evidence. Compare separate
full-frame and region rankings, then judge actual cuts using the
[Match Cuts harness](visual-rhymes.md). The crop proposer separately assesses
whether a retrieved region can be reframed without unacceptable losses.

Before any activation, run an actual pinned checkpoint on a varied subset,
freeze and human-grade pooled candidates against Framing and grounded layout,
measure latency on target hardware, and apply ADR-0008 plus the independent
region/instant gates. This adapter cannot activate production search, update
the active profile, or claim completeness for the film library.
