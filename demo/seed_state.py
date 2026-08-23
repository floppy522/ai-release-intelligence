"""Typed argv-only boundary for the synthetic release-demo seeder.

The shell script retains strict preflight implementation compatibility. This
adapter is the sole public entrypoint and gives tests a replaceable client
boundary without ever interpreting manifest content as shell source.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SeedClient(Protocol):
    """Minimal typed command boundary used by deterministic fake clients."""

    def run(self, argv: Sequence[str], *, environment: dict[str, str]) -> int: ...


@dataclass(frozen=True)
class SubprocessSeedClient:
    """Execute one argv list with shell expansion permanently disabled."""

    def run(self, argv: Sequence[str], *, environment: dict[str, str]) -> int:
        completed = subprocess.run(
            list(argv), check=False, env=environment, shell=False
        )
        return completed.returncode


def main(argv: Sequence[str] | None = None, *, client: SeedClient | None = None) -> int:
    """Launch the strict implementation with a marker that prevents recursion."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    implementation = Path(__file__).with_name("seed_demo_repo.sh")
    environment = dict(os.environ)
    environment["ARI_SEED_IMPLEMENTATION"] = "1"
    runner = client or SubprocessSeedClient()
    return runner.run(
        ["bash", str(implementation), *arguments], environment=environment
    )


if __name__ == "__main__":
    raise SystemExit(main())
