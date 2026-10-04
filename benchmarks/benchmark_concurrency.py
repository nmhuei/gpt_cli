"""Retired live concurrency benchmark.

Automated prompt traffic can trigger upstream abuse detection. Use the `gpt`
CLI manually when an operator explicitly wants to verify a prompt.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "Disabled: scripted live prompts are not permitted; run `gpt` manually instead.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
