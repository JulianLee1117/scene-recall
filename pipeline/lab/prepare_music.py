"""Explicitly download and content-pin Beat This! once; inference never downloads."""
from __future__ import annotations

import argparse
from pathlib import Path
import urllib.request
import uuid

from pipeline.config import load_config
from pipeline.lab.music import content_hash, write_json

CHECKPOINT_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"


def prepare(config):
    root = config.paths.assets_dir / "lab" / "beat-this"
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "profile.json"
    if config.lab.beat_checkpoint is not None or manifest.exists():
        from pipeline.lab.music import _beat_profile
        profile = _beat_profile(config)
        print(f"Verified existing Beat This! profile: {profile['sha256']}")
        return profile
    # Exclusive new path: interrupted downloads never overwrite a valid profile.
    temporary = root / (uuid.uuid4().hex + ".download")
    print("Downloading Beat This! final0 from the authors' published checkpoint host...")
    try:
        with urllib.request.urlopen(CHECKPOINT_URL, timeout=60) as response, temporary.open("xb") as target:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise ValueError("Checkpoint download exceeded the 512 MiB bound")
                target.write(chunk)
        if temporary.stat().st_size < 1024 * 1024:
            raise ValueError("Checkpoint response was unexpectedly small; profile was not activated")
        digest = content_hash(temporary)
        checkpoint = root / f"final0-{digest}.ckpt"
        # Verify the checkpoint format using the restricted weights-only loader.
        import torch
        data = torch.load(temporary, map_location="cpu", weights_only=True)
        if not isinstance(data, dict) or not {"state_dict", "hyper_parameters"} <= data.keys():
            raise ValueError("Downloaded file is not a Beat This! checkpoint")
        if not checkpoint.exists():
            temporary.rename(checkpoint)
        profile = {"model": "beat-this", "package_version": "1.1.0", "source_url": CHECKPOINT_URL,
                   "checkpoint": str(checkpoint.resolve()), "sha256": digest, "contract": "beat-this-minimal-waveform-rms-v1"}
        write_json(manifest, profile)
        print(f"Prepared immutable local checkpoint: {checkpoint}")
        return profile
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    arguments = parser.parse_args()
    prepare(load_config(arguments.config))


if __name__ == "__main__":
    main()
