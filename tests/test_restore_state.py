import unittest
from unittest.mock import patch

from scripts.restore_state import latest_state_run


def artifact(run_id, branch="main", expired=False):
    return {
        "expired": expired,
        "created_at": f"2026-11-09T{run_id:02}:00:00Z",
        "workflow_run": {"id": run_id, "head_branch": branch},
    }


class RestoreStateTests(unittest.TestCase):
    @patch("scripts.restore_state.api")
    def test_latest_monitor_artifact_across_pages_excludes_other_workflows_and_branches(
        self, api
    ):
        api.side_effect = [
            [
                {"artifacts": [artifact(5, branch="feature"), artifact(4)]},
                {"artifacts": [artifact(3, expired=True), artifact(2), artifact(1)]},
            ],
            {"path": ".github/workflows/test.yml", "event": "push"},
            {"path": ".github/workflows/cron.yml", "event": "schedule"},
        ]
        self.assertEqual(latest_state_run("owner/repo", "main"), "2")
        self.assertEqual(api.call_args.args[0], "repos/owner/repo/actions/runs/2")

    @patch("scripts.restore_state.api", return_value=[{"artifacts": []}])
    def test_first_run_has_no_state(self, api):
        self.assertEqual(latest_state_run("owner/repo", "main"), "")

    @patch("scripts.restore_state.api", side_effect=RuntimeError("API unavailable"))
    def test_api_failure_does_not_silently_discard_saved_state(self, api):
        with self.assertRaises(RuntimeError):
            latest_state_run("owner/repo", "main")


if __name__ == "__main__":
    unittest.main()
