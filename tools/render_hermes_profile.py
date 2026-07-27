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


def render(image_reference: str, repository_root: Path) -> str:
    if not IMAGE_PATTERN.fullmatch(image_reference):
        raise ValueError("Hermes image must be an immutable private ECR digest reference")
    template = (
        repository_root / "deploy/xolis/hermes-profile.yaml.in"
    ).read_text(encoding="utf-8")
    return template.replace("__HERMES_IMAGE_REFERENCE__", image_reference)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    args = parser.parse_args()
    print(render(args.image_reference, Path(__file__).resolve().parents[1]), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
