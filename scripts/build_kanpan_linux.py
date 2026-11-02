#!/usr/bin/env python3
from __future__ import annotations

from build_kanpan_bundle import BUILD_DIR, DIST_DIR, build_bundle


def main() -> int:
    return build_bundle(
        "linux",
        dist_dir=DIST_DIR / "linux",
        work_dir=BUILD_DIR / "pyinstaller" / "linux",
        spec_dir=BUILD_DIR / "spec" / "linux",
        console=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
