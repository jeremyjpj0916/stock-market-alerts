"""Find the latest trusted monitor state artifact for the next serialized run."""

import json
import os
import subprocess
from pathlib import Path


def api(path, *flags):
    return json.loads(
        subprocess.check_output(["gh", "api", path, *flags], text=True, timeout=60)
    )


def latest_state_run(repository, branch):
    pages = api(
        f"repos/{repository}/actions/artifacts?name=alert-state&per_page=100",
        "--paginate",
        "--slurp",
    )
    artifacts = sorted(
        (
            artifact
            for page in pages
            for artifact in page["artifacts"]
            if not artifact["expired"]
            and artifact["workflow_run"]["head_branch"] == branch
        ),
        key=lambda artifact: artifact["created_at"],
        reverse=True,
    )
    for artifact in artifacts:
        run_id = artifact["workflow_run"]["id"]
        run = api(f"repos/{repository}/actions/runs/{run_id}")
        if run["path"] == ".github/workflows/cron.yml" and run["event"] in {
            "schedule",
            "workflow_dispatch",
        }:
            return str(run_id)
    return ""


def main():
    run_id = latest_state_run(
        os.environ["GITHUB_REPOSITORY"], os.environ["DEFAULT_BRANCH"]
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"run_id={run_id}\n")
    if not run_id:
        print(
            "::warning::No saved monitor state found; starting a fresh daily alert ledger."
        )


if __name__ == "__main__":
    main()
