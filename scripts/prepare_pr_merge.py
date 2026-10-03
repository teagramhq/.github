#!/usr/bin/env python3
"""Prepare a pull request merge tree for content validation."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        output = "\n".join(
            value.strip() for value in (result.stdout, result.stderr) if value.strip()
        )
        raise RuntimeError(
            f"git {' '.join(arguments)} failed in {repository}: {output}"
        )
    return result.stdout.strip()


def prepare_merge_result(
    candidate: Path, base_repository: Path, base_sha: str, head_sha: str
) -> None:
    actual_head = _git(candidate, "rev-parse", "HEAD")
    if actual_head != head_sha:
        raise RuntimeError(f"Checked out {actual_head} but expected {head_sha}")

    _git(
        candidate,
        "fetch",
        "--no-tags",
        str(base_repository),
        f"{base_sha}:refs/remotes/base/target",
    )
    _git(candidate, "diff", "--check", f"{base_sha}...{head_sha}")
    merged_tree = _git(
        candidate,
        "merge-tree",
        "--write-tree",
        head_sha,
        "refs/remotes/base/target",
    )
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", merged_tree):
        raise RuntimeError(
            f"git merge-tree returned an invalid tree id: {merged_tree!r}"
        )

    _git(candidate, "read-tree", "--reset", "-u", merged_tree)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--base-repository", type=Path, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args()

    try:
        prepare_merge_result(
            args.candidate.resolve(),
            args.base_repository.resolve(),
            args.base_sha,
            args.head_sha,
        )
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
