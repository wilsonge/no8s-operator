"""
GitHub Actions Action Plugin - Implements ActionPlugin for GitHub Actions.

This plugin triggers GitHub Actions workflows and monitors their execution
to completion.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp

from plugins.actions.base import ActionPlugin
from plugins.base import ActionContext, ActionPhase, ActionResult, DriftResult

logger = logging.getLogger(__name__)


class GitHubActionsPlugin(ActionPlugin):
    """
    Action plugin that triggers GitHub Actions workflows.

    Implements the standard ActionPlugin interface for GitHub Actions operations.
    Each resource triggers a workflow and monitors it to completion.
    """

    def __init__(self):
        self.github_token: Optional[str] = None
        self.api_base_url: str = "https://api.github.com"
        self.timeout: int = 3600  # 1 hour default timeout for workflow runs
        self.poll_interval: int = 10  # seconds between status checks
        # Track workflow runs for each resource
        self._workflow_runs: Dict[int, Dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "github_actions"

    @property
    def version(self) -> str:
        return "1.0.0"

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        """Load GitHub Actions plugin configuration from environment variables."""
        return {
            "github_token": os.getenv("GITHUB_TOKEN", ""),
            "api_base_url": os.getenv("GITHUB_API_URL", "https://api.github.com"),
            "timeout": int(os.getenv("GITHUB_ACTIONS_TIMEOUT", "3600")),
            "poll_interval": int(os.getenv("GITHUB_ACTIONS_POLL_INTERVAL", "10")),
        }

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the plugin with configuration."""
        self.github_token = config.get("github_token")
        self.api_base_url = config.get("api_base_url", self.api_base_url)
        self.timeout = config.get("timeout", self.timeout)
        self.poll_interval = config.get("poll_interval", self.poll_interval)

        if not self.github_token:
            logger.warning(
                "GitHub token not configured. Set GITHUB_TOKEN environment variable."
            )

        logger.debug(
            f"GitHub Actions plugin initialized: api_base_url={self.api_base_url}, "
            f"timeout={self.timeout}s, poll_interval={self.poll_interval}s"
        )

    async def validate_spec(self, spec: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that a resource spec is valid for GitHub Actions."""
        required_fields = ["owner", "repo", "workflow"]
        missing = [f for f in required_fields if f not in spec]

        if missing:
            return False, f"Spec must contain fields: {', '.join(missing)}"

        # Workflow can be an ID or filename
        workflow = spec.get("workflow")
        if not isinstance(workflow, (str, int)):
            return False, "Workflow must be a string (filename) or integer (ID)"

        return True, None

    async def prepare(self, ctx: ActionContext) -> Dict[str, Any]:
        """
        Prepare for workflow execution.

        Returns a workspace dict containing the parsed spec and API endpoints.
        """
        owner = ctx.spec.get("owner")
        repo = ctx.spec.get("repo")
        workflow = ctx.spec.get("workflow")
        ref = ctx.spec.get("ref", "main")
        inputs = ctx.spec.get("inputs", {})

        workspace = {
            "owner": owner,
            "repo": repo,
            "workflow": workflow,
            "ref": ref,
            "inputs": inputs,
            "resource_id": ctx.resource_id,
            "resource_name": ctx.resource_name,
            "dispatch_url": (
                f"{self.api_base_url}/repos/{owner}/{repo}"
                f"/actions/workflows/{workflow}/dispatches"
            ),
            "runs_url": (
                f"{self.api_base_url}/repos/{owner}/{repo}"
                f"/actions/workflows/{workflow}/runs"
            ),
        }

        logger.info(
            f"Prepared GitHub Actions workspace for {owner}/{repo}, "
            f"workflow: {workflow}"
        )
        return workspace

    async def plan(self, ctx: ActionContext, workspace: Dict[str, Any]) -> ActionResult:
        """
        Plan the workflow execution.

        For GitHub Actions, planning validates the workflow exists and is accessible.
        """
        result = ActionResult(phase=ActionPhase.PLANNING)

        try:
            # Verify workflow exists and we have access
            workflow_info = await self._get_workflow_info(workspace)

            if workflow_info:
                result.success = True
                result.has_changes = True  # Always trigger workflow on apply
                result.plan_output = (
                    f"Will trigger workflow: {workflow_info.get('name', 'unknown')}\n"
                    f"Repository: {workspace['owner']}/{workspace['repo']}\n"
                    f"Ref: {workspace['ref']}\n"
                    f"Inputs: {json.dumps(workspace['inputs'], indent=2)}"
                )
            else:
                result.success = False
                result.phase = ActionPhase.FAILED
                result.error_message = (
                    f"Workflow '{workspace['workflow']}' not found or inaccessible"
                )

        except Exception as e:
            logger.error(f"Error during plan: {e}")
            result.success = False
            result.phase = ActionPhase.FAILED
            result.error_message = str(e)

        return result

    async def apply(
        self, ctx: ActionContext, workspace: Dict[str, Any]
    ) -> ActionResult:
        """
        Trigger the GitHub Actions workflow and wait for completion.
        """
        result = ActionResult(phase=ActionPhase.APPLYING)

        try:
            # Trigger the workflow
            run_id = await self._trigger_workflow(workspace)

            if not run_id:
                result.success = False
                result.phase = ActionPhase.FAILED
                result.error_message = "Failed to trigger workflow"
                return result

            # Store run info for tracking
            self._workflow_runs[ctx.resource_id] = {
                "run_id": run_id,
                "workspace": workspace,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

            # Wait for workflow completion
            final_status = await self._wait_for_completion(workspace, run_id)

            if final_status["conclusion"] == "success":
                result.success = True
                result.phase = ActionPhase.COMPLETED
                result.apply_output = (
                    f"Workflow run {run_id} completed successfully\n"
                    f"URL: {final_status.get('html_url', 'N/A')}"
                )
                result.resources_updated = 1

                # Get workflow outputs if available
                result.outputs = await self._get_workflow_outputs(workspace, run_id)
            else:
                result.success = False
                result.phase = ActionPhase.FAILED
                result.error_message = (
                    f"Workflow run {run_id} failed with conclusion: "
                    f"{final_status['conclusion']}"
                )
                result.apply_output = f"URL: {final_status.get('html_url', 'N/A')}"

        except asyncio.TimeoutError:
            result.success = False
            result.phase = ActionPhase.FAILED
            result.error_message = f"Workflow timed out after {self.timeout}s"
        except Exception as e:
            logger.error(f"Error during apply: {e}")
            result.success = False
            result.phase = ActionPhase.FAILED
            result.error_message = str(e)

        return result

    async def destroy(
        self, ctx: ActionContext, workspace: Dict[str, Any]
    ) -> ActionResult:
        """
        Cancel any running workflow for this resource.

        For GitHub Actions, destroy means cancelling the active run if any.
        """
        result = ActionResult(phase=ActionPhase.DESTROYING)

        try:
            run_info = self._workflow_runs.get(ctx.resource_id)

            if run_info:
                cancelled = await self._cancel_workflow_run(
                    workspace, run_info["run_id"]
                )
                if cancelled:
                    result.apply_output = f"Cancelled workflow run {run_info['run_id']}"
                    result.resources_deleted = 1
                else:
                    result.apply_output = "No active workflow run to cancel"

            result.success = True
            result.phase = ActionPhase.COMPLETED

        except Exception as e:
            logger.error(f"Error during destroy: {e}")
            result.success = False
            result.phase = ActionPhase.FAILED
            result.error_message = str(e)

        return result

    async def get_outputs(
        self, ctx: ActionContext, workspace: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get outputs from the last workflow run."""
        run_info = self._workflow_runs.get(ctx.resource_id)

        if not run_info:
            return {}

        return await self._get_workflow_outputs(workspace, run_info["run_id"])

    async def get_state(
        self, ctx: ActionContext, workspace: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Get the current state of the workflow run."""
        run_info = self._workflow_runs.get(ctx.resource_id)

        if not run_info:
            return None

        try:
            run_status = await self._get_run_status(workspace, run_info["run_id"])
            return {
                "run_id": run_info["run_id"],
                "status": run_status.get("status"),
                "conclusion": run_status.get("conclusion"),
                "html_url": run_status.get("html_url"),
                "started_at": run_info["started_at"],
            }
        except Exception as e:
            logger.error(f"Error getting state: {e}")
            return None

    async def cleanup(self, workspace: Dict[str, Any]) -> None:
        """Clean up tracking data for this resource."""
        resource_id = workspace.get("resource_id")
        if resource_id and resource_id in self._workflow_runs:
            del self._workflow_runs[resource_id]
            logger.info(f"Cleaned up workflow tracking for resource {resource_id}")

    async def detect_drift(
        self, ctx: ActionContext, workspace: Dict[str, Any]
    ) -> DriftResult:
        """
        Detect drift for GitHub Actions.

        For workflow triggers, drift is detected if the spec has changed
        since the last run (based on spec hash comparison).
        """
        result = DriftResult()

        run_info = self._workflow_runs.get(ctx.resource_id)
        if run_info:
            stored_inputs = run_info.get("workspace", {}).get("inputs", {})
            current_inputs = workspace.get("inputs", {})

            if stored_inputs != current_inputs:
                result.has_drift = True
                result.drift_details = "Workflow inputs have changed since last run"
                result.resources_drifted = 1

        return result

    # Private helper methods

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for GitHub API requests."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def _get_workflow_info(
        self, workspace: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Get workflow information to verify it exists."""
        owner = workspace["owner"]
        repo = workspace["repo"]
        workflow = workspace["workflow"]

        url = f"{self.api_base_url}/repos/{owner}/{repo}/actions/workflows/{workflow}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._get_headers()) as response:
                if response.status == 200:
                    return await response.json()
                logger.warning(
                    f"Failed to get workflow info: {response.status} - "
                    f"{await response.text()}"
                )
                return None

    async def _trigger_workflow(self, workspace: Dict[str, Any]) -> Optional[int]:
        """Trigger a workflow dispatch and return the run ID."""
        # Get runs before triggering to find the new run
        before_runs = await self._get_recent_runs(workspace)
        before_run_ids = {r["id"] for r in before_runs}

        # Trigger the workflow
        payload = {
            "ref": workspace["ref"],
            "inputs": workspace["inputs"],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                workspace["dispatch_url"],
                headers=self._get_headers(),
                json=payload,
            ) as response:
                if response.status not in (204, 200):
                    error_text = await response.text()
                    logger.error(
                        f"Failed to trigger workflow: {response.status} - {error_text}"
                    )
                    return None

        # Poll for the new run to appear
        for _ in range(30):  # Wait up to 30 seconds for run to appear
            await asyncio.sleep(1)
            after_runs = await self._get_recent_runs(workspace)

            for run in after_runs:
                if run["id"] not in before_run_ids:
                    logger.info(f"Workflow run started: {run['id']}")
                    return run["id"]

        logger.error("Workflow was triggered but run ID could not be determined")
        return None

    async def _get_recent_runs(self, workspace: Dict[str, Any]) -> list:
        """Get recent workflow runs."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                workspace["runs_url"],
                headers=self._get_headers(),
                params={"per_page": 10},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("workflow_runs", [])
                return []

    async def _get_run_status(
        self, workspace: Dict[str, Any], run_id: int
    ) -> Dict[str, Any]:
        """Get the status of a workflow run."""
        owner = workspace["owner"]
        repo = workspace["repo"]
        url = f"{self.api_base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._get_headers()) as response:
                if response.status == 200:
                    return await response.json()
                raise Exception(f"Failed to get run status: {response.status}")

    async def _wait_for_completion(
        self, workspace: Dict[str, Any], run_id: int
    ) -> Dict[str, Any]:
        """Wait for a workflow run to complete."""
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.timeout:
                raise asyncio.TimeoutError()

            status = await self._get_run_status(workspace, run_id)

            if status["status"] == "completed":
                return status

            logger.debug(
                f"Workflow run {run_id} status: {status['status']}, "
                f"waiting {self.poll_interval}s..."
            )
            await asyncio.sleep(self.poll_interval)

    async def _cancel_workflow_run(
        self, workspace: Dict[str, Any], run_id: int
    ) -> bool:
        """Cancel a workflow run."""
        owner = workspace["owner"]
        repo = workspace["repo"]
        url = f"{self.api_base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/cancel"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._get_headers()) as response:
                if response.status == 202:
                    logger.info(f"Cancelled workflow run {run_id}")
                    return True
                logger.warning(f"Failed to cancel workflow run: {response.status}")
                return False

    async def _get_workflow_outputs(
        self, workspace: Dict[str, Any], run_id: int
    ) -> Dict[str, Any]:
        """
        Get outputs from a workflow run.

        Note: GitHub Actions doesn't have a direct way to get workflow outputs.
        This retrieves job outputs and artifacts metadata.
        """
        owner = workspace["owner"]
        repo = workspace["repo"]
        outputs = {}

        # Get jobs for this run
        jobs_url = (
            f"{self.api_base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(jobs_url, headers=self._get_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    jobs = data.get("jobs", [])

                    outputs["jobs"] = [
                        {
                            "name": job["name"],
                            "status": job["status"],
                            "conclusion": job["conclusion"],
                            "started_at": job.get("started_at"),
                            "completed_at": job.get("completed_at"),
                        }
                        for job in jobs
                    ]

            # Get artifacts
            artifacts_url = (
                f"{self.api_base_url}/repos/{owner}/{repo}"
                f"/actions/runs/{run_id}/artifacts"
            )
            async with session.get(
                artifacts_url, headers=self._get_headers()
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    artifacts = data.get("artifacts", [])

                    outputs["artifacts"] = [
                        {
                            "name": artifact["name"],
                            "size_in_bytes": artifact["size_in_bytes"],
                            "archive_download_url": artifact["archive_download_url"],
                        }
                        for artifact in artifacts
                    ]

        return outputs
