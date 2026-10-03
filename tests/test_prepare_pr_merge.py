from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-validation.yml"
RESOLVE_STEP = "      - name: Resolve pull request revisions"
MERGE_STEP = "      - name: Check PR whitespace and prepare its merge result"
PUBLISH_STEP = "      - name: Publish dispatched PR status"


def _workflow_step_script(step_name: str) -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step_start = lines.index(step_name)
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


def _workflow_job_text(job_name: str) -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {job_name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ")
            and len(lines[index]) > 2
            and lines[index][2] != " "
        ),
        len(lines),
    )
    return "\n".join(lines[start:end]) + "\n"


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
        self.identity = {
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
        self._git(self.base, "commit", "--quiet", "-m", "initial", env=self.identity)
        self.recorded_base_sha = self._git(
            self.base, "rev-parse", "HEAD", env=self.environment
        ).stdout.strip()
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
            env=self.identity,
        )

        (self.base / "main-change.txt").write_text("main advanced\n", encoding="utf-8")
        (self.base / "tests").mkdir()
        (self.base / "tests/target_fixture.py").write_text(
            "# Fixture supplied by the newer target branch.\n", encoding="utf-8"
        )
        (self.base / "scripts").mkdir()
        (self.base / "scripts/validate_brand_assets.py").write_text(
            "# Validator supplied by the newer target branch.\n", encoding="utf-8"
        )
        self._git(
            self.base,
            "add",
            "main-change.txt",
            "tests",
            "scripts",
            env=self.environment,
        )
        self._git(
            self.base,
            "commit",
            "--quiet",
            "-m",
            "advance main",
            env=self.identity,
        )
        self.base_sha = self._git(self.base, "rev-parse", "HEAD").stdout.strip()
        self.head_sha = self._git(self.candidate, "rev-parse", "HEAD").stdout.strip()
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self._install_fake_gh()

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

    def _install_fake_gh(self) -> None:
        gh = self.fake_bin / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "endpoint = next((arg for arg in args if arg.startswith('repos/')), '')\n"
            "pull_endpoint = f\"repos/{os.environ['GITHUB_REPOSITORY']}/pulls/{os.environ['PR_NUMBER']}\"\n"
            "commits_endpoint = f\"repos/{os.environ['GITHUB_REPOSITORY']}/commits\"\n"
            "if endpoint == pull_endpoint:\n"
            "    print(Path(os.environ['PR_METADATA_FILE']).read_text(encoding='utf-8'))\n"
            "elif endpoint == commits_endpoint:\n"
            "    if os.environ.get('FAIL_TARGET_API') == '1':\n"
            "        print('target API unavailable', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    expected_query = f\"sha={os.environ['EXPECTED_BASE_BRANCH']}\"\n"
            "    if expected_query not in args or 'per_page=1' not in args:\n"
            "        print('unexpected target revision query', file=sys.stderr)\n"
            "        sys.exit(2)\n"
            "    print(json.dumps([{'sha': os.environ['TARGET_SHA']}]))\n"
            "else:\n"
            "    print(f'unexpected gh command: {args}', file=sys.stderr)\n"
            "    sys.exit(2)\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def _run_resolve_step(
        self,
        *,
        fail_target_api: bool = False,
        expected_head_sha: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
        output_file = self.root / "github_output"
        summary_file = self.root / "github_step_summary"
        metadata_file = self.root / "pull_request.json"
        metadata_file.write_text(
            json.dumps(
                {
                    "state": "open",
                    "base": {
                        "sha": self.recorded_base_sha,
                        "ref": "main",
                        "repo": {"full_name": "teagramhq/.github"},
                    },
                    "head": {
                        "sha": self.head_sha,
                        "repo": {"full_name": "teagramhq/.github"},
                    },
                }
            ),
            encoding="utf-8",
        )
        environment = {
            **self.environment,
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "EVENT_NAME": "workflow_dispatch",
            "PR_NUMBER": "1",
            "EVENT_PR_NUMBER": "",
            "EVENT_BASE_SHA": "",
            "EVENT_BASE_REF": "",
            "EVENT_BASE_REPOSITORY": "",
            "EVENT_HEAD_SHA": "",
            "EVENT_HEAD_REPOSITORY": "",
            "EXPECTED_HEAD_SHA": expected_head_sha or self.head_sha,
            "GITHUB_REPOSITORY": "teagramhq/.github",
            "PR_METADATA_FILE": str(metadata_file),
            "EXPECTED_BASE_BRANCH": "main",
            "TARGET_SHA": self.base_sha,
            "FAIL_TARGET_API": "1" if fail_target_api else "0",
            "GITHUB_OUTPUT": str(output_file),
            "GITHUB_STEP_SUMMARY": str(summary_file),
        }
        result = subprocess.run(
            ["bash", "-c", _workflow_step_script(RESOLVE_STEP)],
            check=False,
            text=True,
            capture_output=True,
            env=environment,
        )
        outputs = {}
        if output_file.exists():
            outputs = dict(
                line.split("=", 1)
                for line in output_file.read_text(encoding="utf-8").splitlines()
            )
        summary = (
            summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""
        )
        return result, outputs, summary

    def _run_merge_step(
        self, *, base_sha: str | None = None, head_sha: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **self.environment,
            "GITHUB_WORKSPACE": str(self.root),
            "BASE_SHA": base_sha or self.base_sha,
            "HEAD_SHA": head_sha or self.head_sha,
            "PR_NUMBER": "1",
        }
        return subprocess.run(
            ["bash", "-c", _workflow_step_script(MERGE_STEP)],
            check=False,
            text=True,
            capture_output=True,
            env=environment,
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

        result = self._run_merge_step()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.candidate / "main-change.txt").read_text(encoding="utf-8"),
            "main advanced\n",
        )
        self.assertEqual(
            (self.candidate / "pr-change.txt").read_text(encoding="utf-8"),
            "candidate change\n",
        )
        self.assertTrue((self.candidate / "tests/target_fixture.py").exists())
        self.assertTrue((self.candidate / "scripts/validate_brand_assets.py").exists())
        self.assertEqual(
            self._git(self.candidate, "rev-parse", "HEAD").stdout.strip(),
            self.head_sha,
        )

    def test_dispatch_resolves_new_target_tip_for_candidate_with_old_base(self) -> None:
        result, outputs, summary = self._run_resolve_step()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["base_sha"], self.base_sha)
        self.assertNotEqual(outputs["base_sha"], self.recorded_base_sha)
        self.assertEqual(outputs["base_ref"], self.base_sha)
        self.assertEqual(outputs["head_sha"], self.head_sha)
        self.assertIn(
            f"- Target: `teagramhq/.github/main` at `{self.base_sha}`", summary
        )
        self.assertIn(f"- Head: `teagramhq/.github` at `{self.head_sha}`", summary)

        merge_result = self._run_merge_step(
            base_sha=outputs["base_sha"], head_sha=outputs["head_sha"]
        )

        self.assertEqual(merge_result.returncode, 0, merge_result.stderr)
        self.assertTrue((self.candidate / "tests/target_fixture.py").exists())
        self.assertTrue((self.candidate / "scripts/validate_brand_assets.py").exists())
        self.assertEqual(
            self._git(self.candidate, "rev-parse", "HEAD").stdout.strip(),
            self.head_sha,
        )

    def test_dispatch_reports_target_api_failure(self) -> None:
        result, _, _ = self._run_resolve_step(fail_target_api=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not resolve current tip", result.stdout)

    def test_dispatch_rejects_a_head_different_from_the_requested_sha(self) -> None:
        result, outputs, _ = self._run_resolve_step(expected_head_sha="c" * 40)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("head changed", result.stdout)
        self.assertEqual(outputs, {})

    def test_candidate_trailing_whitespace_fails_validation(self) -> None:
        (self.candidate / "pr-change.txt").write_text(
            "candidate change \n", encoding="utf-8"
        )
        self._git(self.candidate, "add", "pr-change.txt", env=self.environment)
        self._git(
            self.candidate,
            "commit",
            "--amend",
            "--quiet",
            "--no-edit",
            env=self.identity,
        )
        self.head_sha = self._git(self.candidate, "rev-parse", "HEAD").stdout.strip()

        result = self._run_merge_step()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("trailing whitespace", result.stdout + result.stderr)


class PRStatusPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.pr_file = self.root / "pull_request.json"
        self.calls_file = self.root / "api_calls.json"
        self.calls_file.write_text("[]", encoding="utf-8")
        self.head_sha = "a" * 40
        self.target_sha = "b" * 40
        self._install_fake_gh()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _install_fake_gh(self) -> None:
        gh = self.fake_bin / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "method = args[args.index('--method') + 1] if '--method' in args else 'GET'\n"
            "endpoint = next((arg for arg in args if arg.startswith('repos/')), '')\n"
            "params = {}\n"
            "for index, arg in enumerate(args[:-1]):\n"
            "    if arg in ('-f', '-F') and '=' in args[index + 1]:\n"
            "        key, value = args[index + 1].split('=', 1)\n"
            "        params[key] = value\n"
            "calls_path = Path(os.environ['API_CALLS_FILE'])\n"
            "calls = json.loads(calls_path.read_text(encoding='utf-8'))\n"
            "calls.append({'method': method, 'endpoint': endpoint, 'params': params})\n"
            "calls_path.write_text(json.dumps(calls), encoding='utf-8')\n"
            "repo = os.environ['GITHUB_REPOSITORY']\n"
            "if method == 'GET' and endpoint == f\"repos/{repo}/pulls/{os.environ['PR_NUMBER']}\":\n"
            "    if os.environ.get('FAIL_API') == 'pull':\n"
            "        print('pull request API unavailable', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    print(Path(os.environ['PR_JSON_FILE']).read_text(encoding='utf-8'))\n"
            "elif method == 'GET' and endpoint == f\"repos/{repo}/commits\":\n"
            "    if os.environ.get('FAIL_API') == 'target':\n"
            "        print('target API unavailable', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    if params.get('sha') != os.environ['DEFAULT_BRANCH'] or params.get('per_page') != '1':\n"
            "        print('unexpected target revision query', file=sys.stderr)\n"
            "        sys.exit(2)\n"
            "    print(json.dumps([{'sha': os.environ['LIVE_TARGET_SHA']}]))\n"
            "elif method == 'POST' and endpoint == f\"repos/{repo}/statuses/{os.environ['EXPECTED_HEAD_SHA']}\":\n"
            "    if os.environ.get('FAIL_API') == 'status':\n"
            "        print('status API unavailable', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            "    print(json.dumps({'id': len(calls), **params}))\n"
            "else:\n"
            "    print(f'unexpected gh command: {args}', file=sys.stderr)\n"
            "    sys.exit(2)\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def _run_publisher(
        self,
        *,
        content_result: str = "success",
        verify_result: str = "success",
        pr_state: str = "open",
        pr_base_repository: str = "teagramhq/.github",
        pr_base_ref: str = "main",
        pr_head_sha: str | None = None,
        live_target_sha: str | None = None,
        expected_head_sha: str | None = None,
        expected_target_sha: str | None = None,
        expected_target_ref: str = "main",
        default_branch: str = "main",
        fail_api: str = "",
    ) -> subprocess.CompletedProcess[str]:
        head_sha = expected_head_sha or self.head_sha
        target_sha = expected_target_sha or self.target_sha
        self.pr_file.write_text(
            json.dumps(
                {
                    "state": pr_state,
                    "base": {
                        "ref": pr_base_ref,
                        "repo": {"full_name": pr_base_repository},
                    },
                    "head": {"sha": pr_head_sha or head_sha},
                }
            ),
            encoding="utf-8",
        )
        environment = {
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "teagramhq/.github",
            "PR_NUMBER": "7",
            "EXPECTED_HEAD_SHA": head_sha,
            "EXPECTED_TARGET_REF": expected_target_ref,
            "EXPECTED_TARGET_SHA": target_sha,
            "DEFAULT_BRANCH": default_branch,
            "CONTENT_RESULT": content_result,
            "VERIFY_RESULT": verify_result,
            "STATUS_CONTEXT": "PR validation / dispatched-merge",
            "RUN_ATTEMPT": "2",
            "RUN_ID": "12345",
            "SERVER_URL": "https://github.com",
            "LIVE_TARGET_SHA": live_target_sha or target_sha,
            "FAIL_API": fail_api,
            "PR_JSON_FILE": str(self.pr_file),
            "API_CALLS_FILE": str(self.calls_file),
        }
        return subprocess.run(
            ["bash", "-c", _workflow_step_script(PUBLISH_STEP)],
            check=False,
            text=True,
            capture_output=True,
            env=environment,
        )

    def _api_calls(self) -> list[dict[str, object]]:
        return json.loads(self.calls_file.read_text(encoding="utf-8"))

    def _status_calls(self) -> list[dict[str, object]]:
        return [call for call in self._api_calls() if call["method"] == "POST"]

    def test_publisher_permission_and_trigger_boundary(self) -> None:
        publisher = _workflow_job_text("publish-head-status")
        content = _workflow_job_text("content")
        verify = _workflow_job_text("verify-dispatched-head")

        self.assertIn("statuses: write", publisher)
        self.assertIn("pull-requests: read", publisher)
        self.assertIn("contents: read", publisher)
        self.assertNotIn("statuses: write", content)
        self.assertNotIn("statuses: write", verify)
        self.assertIn(
            "if: always() && !cancelled() && github.event_name == 'workflow_dispatch' "
            "&& github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
            publisher,
        )
        self.assertIn("needs: [content, verify-dispatched-head]", publisher)
        self.assertIn("cancel-in-progress: false", publisher)
        self.assertNotIn("\n        uses:", publisher)
        self.assertNotIn("checkout", publisher)
        self.assertNotIn("artifact", publisher)
        self.assertNotIn("cache", publisher)

    def test_publisher_maps_success_and_job_failures(self) -> None:
        cases = (
            ("success", "success", "success", "validation passed"),
            ("failure", "success", "failure", "content validation failed"),
            ("success", "failure", "failure", "PR verification failed"),
            (
                "failure",
                "failure",
                "failure",
                "content validation and PR verification failed",
            ),
        )
        for content_result, verify_result, expected_state, phrase in cases:
            with self.subTest(content=content_result, verify=verify_result):
                self.calls_file.write_text("[]", encoding="utf-8")
                result = self._run_publisher(
                    content_result=content_result, verify_result=verify_result
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                status_calls = self._status_calls()
                self.assertEqual(len(status_calls), 1)
                status = status_calls[0]["params"]
                self.assertEqual(status["state"], expected_state)
                self.assertEqual(status["context"], "PR validation / dispatched-merge")
                self.assertIn(self.target_sha[:12], status["description"])
                self.assertIn("attempt 2", status["description"])
                self.assertIn(phrase, status["description"])
                self.assertEqual(
                    status["target_url"],
                    "https://github.com/teagramhq/.github/actions/runs/12345/attempts/2",
                )

    def test_publisher_suppresses_cancelled_or_skipped_results(self) -> None:
        for content_result, verify_result in (
            ("cancelled", "success"),
            ("success", "cancelled"),
            ("success", "skipped"),
        ):
            with self.subTest(content=content_result, verify=verify_result):
                self.calls_file.write_text("[]", encoding="utf-8")
                result = self._run_publisher(
                    content_result=content_result, verify_result=verify_result
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._status_calls(), [])

    def test_stale_pr_head_or_target_never_publishes(self) -> None:
        stale_head = self._run_publisher(pr_head_sha="c" * 40)
        self.assertNotEqual(stale_head.returncode, 0)
        self.assertEqual(self._status_calls(), [])

        self.calls_file.write_text("[]", encoding="utf-8")
        stale_target = self._run_publisher(live_target_sha="c" * 40)
        self.assertNotEqual(stale_target.returncode, 0)
        self.assertIn("Target branch advanced", stale_target.stdout)
        self.assertEqual(self._status_calls(), [])

    def test_invalid_repository_target_and_api_errors_never_publish(self) -> None:
        cases = (
            {"pr_state": "closed"},
            {"pr_base_repository": "attacker.example/repo"},
            {"pr_base_ref": "release"},
            {"expected_target_ref": "release"},
            {"expected_target_sha": "not-a-sha"},
            {"fail_api": "pull"},
            {"fail_api": "target"},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.calls_file.write_text("[]", encoding="utf-8")
                result = self._run_publisher(**kwargs)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._status_calls(), [])

    def test_status_api_error_fails_publication(self) -> None:
        result = self._run_publisher(fail_api="status")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not publish", result.stdout)
        self.assertEqual(len(self._status_calls()), 1)

    def test_current_pair_retry_republishes_same_context_and_result(self) -> None:
        first = self._run_publisher()
        second = self._run_publisher()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        status_calls = self._status_calls()
        self.assertEqual(len(status_calls), 2)
        self.assertEqual(status_calls[0], status_calls[1])
        self.assertEqual(
            status_calls[0]["endpoint"],
            f"repos/teagramhq/.github/statuses/{self.head_sha}",
        )


if __name__ == "__main__":
    unittest.main()
