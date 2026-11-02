#!/usr/bin/env python3
from __future__ import annotations

from build_kanpan_bundle import BUILD_DIR, DIST_DIR, build_bundle


def main() -> int:
    return build_bundle(
        "windows",
        dist_dir=DIST_DIR,
        work_dir=BUILD_DIR / "pyinstaller",
        spec_dir=BUILD_DIR / "spec",
        console=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
