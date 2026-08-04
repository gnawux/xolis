#!/usr/bin/env python3
"""Render the opt-in Hermes profile with an immutable private image reference."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


IMAGE_PATTERN = re.compile(
    r"^[0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/"
    r"xolis/xolis-runtime-hermes@sha256:[0-9a-f]{64}$"
)


def render(image_reference: str, repository_root: Path, image_mode: str = "oci") -> str:
    if not IMAGE_PATTERN.fullmatch(image_reference):
        raise ValueError("Hermes image must be an immutable private ECR digest reference")
    template = (
        repository_root / "deploy/xolis/hermes-profile.yaml.in"
    ).read_text(encoding="utf-8")
    modes = {
        "oci": ("hermes-agent-v1", "xolis-kata"),
        "nydus": ("hermes-agent-nydus-v1", "xolis-kata-nydus"),
        "pvm": ("hermes-agent-pvm-v1", "xolis-kata-pvm"),
    }
    if image_mode not in modes:
        raise ValueError("image mode must be 'oci', 'nydus', or 'pvm'")
    profile, runtime_class = modes[image_mode]
    return (
        template.replace("__HERMES_IMAGE_REFERENCE__", image_reference)
        .replace("__HERMES_PROFILE__", profile)
        .replace("__IMAGE_MODE__", image_mode)
        .replace("__RUNTIME_CLASS__", runtime_class)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument(
        "--image-mode", choices=("oci", "nydus", "pvm"), default="oci"
    )
    args = parser.parse_args()
    print(
        render(
            args.image_reference,
            Path(__file__).resolve().parents[1],
            args.image_mode,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
