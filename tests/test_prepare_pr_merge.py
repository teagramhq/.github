from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-validation.yml"
MERGE_STEP = "      - name: Check PR whitespace and prepare its merge result"


def _merge_step_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step_start = lines.index(MERGE_STEP)
    run_start = next(
        index
        for index in range(step_start, len(lines))
        if lines[index] == "        run: |"
    )

    script_lines: list[str] = []
    for line in lines[run_start + 1 :]:
        if line.startswith("      - name: "):
            break
        if line.startswith("          "):
            script_lines.append(line[10:])
        elif not line.strip():
            script_lines.append("")
        else:
            raise AssertionError(f"Unexpected indentation in workflow step: {line!r}")

    return "\n".join(script_lines) + "\n"


class PRMergePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.base = self.root / "base"
        self.candidate = self.root / "candidate"
        self.home = self.root / "home"
        self.home.mkdir()
        self.empty_git_config = self.root / "empty.gitconfig"
        self.empty_git_config.write_text("", encoding="utf-8")
        self.environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.empty_git_config),
        }
        identity = {
            **self.environment,
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.test",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.test",
        }

        self.base.mkdir()
        self._git(self.base, "init", "--quiet", "-b", "main", env=self.environment)
        (self.base / "README.md").write_text("old candidate\n", encoding="utf-8")
        self._git(self.base, "add", "README.md", env=self.environment)
        self._git(self.base, "commit", "--quiet", "-m", "initial", env=identity)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.base), str(self.candidate)],
            check=True,
            env=self.environment,
        )
        self._git(self.candidate, "switch", "--quiet", "-c", "feature")
        (self.candidate / "pr-change.txt").write_text(
            "candidate change\n", encoding="utf-8"
        )
        self._git(self.candidate, "add", "pr-change.txt", env=self.environment)
        self._git(
            self.candidate,
            "commit",
            "--quiet",
            "-m",
            "candidate change",
            env=identity,
        )

        (self.base / "main-change.txt").write_text("main advanced\n", encoding="utf-8")
        self._git(self.base, "add", "main-change.txt", env=self.environment)
        self._git(self.base, "commit", "--quiet", "-m", "advance main", env=identity)
        self.base_sha = self._git(self.base, "rev-parse", "HEAD").stdout.strip()
        self.head_sha = self._git(self.candidate, "rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _git(
        repository: Path,
        *arguments: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_divergent_old_candidate_merges_without_candidate_ci_scripts(self) -> None:
        self.assertFalse((self.candidate / "scripts/prepare_pr_merge.py").exists())
        self._git(
            self.candidate,
            "fetch",
            "--quiet",
            str(self.base),
            f"{self.base_sha}:refs/remotes/base/target",
            env=self.environment,
        )
        merge_base = self._git(
            self.candidate, "merge-base", self.base_sha, self.head_sha
        ).stdout.strip()
        self.assertNotEqual(merge_base, self.base_sha)
        self.assertNotEqual(merge_base, self.head_sha)

        environment = {
            **self.environment,
            "GITHUB_WORKSPACE": str(self.root),
            "BASE_SHA": self.base_sha,
            "HEAD_SHA": self.head_sha,
        }
        result = subprocess.run(
            ["bash", "-c", _merge_step_script()],
            check=False,
            text=True,
            capture_output=True,
            env=environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.candidate / "main-change.txt").read_text(encoding="utf-8"),
            "main advanced\n",
        )
        self.assertEqual(
            (self.candidate / "pr-change.txt").read_text(encoding="utf-8"),
            "candidate change\n",
        )
        self.assertEqual(
            self._git(self.candidate, "rev-parse", "HEAD").stdout.strip(),
            self.head_sha,
        )


if __name__ == "__main__":
    unittest.main()
