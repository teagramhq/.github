from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_pr_merge import prepare_merge_result


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
        self._git(self.base, "init", "--quiet", "-b", "main")
        (self.base / "base.txt").write_text("initial\n", encoding="utf-8")
        self._git(self.base, "add", "base.txt", env=identity)
        self._git(self.base, "commit", "--quiet", "-m", "initial", env=identity)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.base), str(self.candidate)],
            check=True,
            env=self.environment,
        )
        self._git(self.candidate, "switch", "--quiet", "-c", "feature")
        (self.candidate / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git(self.candidate, "add", "feature.txt", env=identity)
        self._git(self.candidate, "commit", "--quiet", "-m", "feature", env=identity)

        (self.base / "main.txt").write_text("main advanced\n", encoding="utf-8")
        self._git(self.base, "add", "main.txt", env=identity)
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

    def test_divergent_merge_prepares_tree_without_committer_identity(self) -> None:
        self._git(
            self.candidate,
            "fetch",
            "--quiet",
            str(self.base),
            f"{self.base_sha}:refs/remotes/base/target",
        )
        merge_base = self._git(
            self.candidate, "merge-base", self.base_sha, self.head_sha
        ).stdout.strip()
        self.assertNotEqual(merge_base, self.base_sha)
        self.assertNotEqual(merge_base, self.head_sha)

        with patch.dict(os.environ, self.environment, clear=True):
            prepare_merge_result(
                self.candidate, self.base, self.base_sha, self.head_sha
            )

        self.assertEqual(
            (self.candidate / "main.txt").read_text(encoding="utf-8"),
            "main advanced\n",
        )
        self.assertEqual(
            (self.candidate / "feature.txt").read_text(encoding="utf-8"),
            "feature\n",
        )
        self.assertEqual(
            self._git(self.candidate, "rev-parse", "HEAD").stdout.strip(),
            self.head_sha,
        )


if __name__ == "__main__":
    unittest.main()
