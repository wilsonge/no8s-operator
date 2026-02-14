"""
Admission Webhooks - Validating and mutating webhook chain.

Intercepts resource mutations before persistence, calling external HTTP
webhooks for validation and/or mutation. Similar to Kubernetes admission
controllers.
"""

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class AdmissionError(Exception):
    """Raised when an admission webhook denies a request."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class AdmissionRequest:
    """Request sent to an admission webhook."""

    operation: str
    resource: Dict[str, Any]
    old_resource: Optional[Dict[str, Any]] = None


@dataclass
class AdmissionResponse:
    """Response from an admission webhook."""

    allowed: bool
    message: str = ""
    patches: List[Dict[str, Any]] = field(default_factory=list)


def apply_patches(
    spec: Dict[str, Any], patches: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Apply JSON Patch operations to a spec dict.

    Supports add, replace, and remove operations with /spec/... paths.
    Path segments are separated by '/'. The leading '/spec/' prefix is
    stripped if present, so patches target fields within the spec.

    Args:
        spec: The resource spec to patch.
        patches: List of JSON Patch operations.

    Returns:
        A new spec dict with patches applied.

    Raises:
        AdmissionError: If a patch operation is invalid.
    """
    result = copy.deepcopy(spec)

    for patch in patches:
        op = patch.get("op")
        path = patch.get("path", "")

        # Strip leading /spec/ if present, otherwise strip leading /
        if path.startswith("/spec/"):
            path = path[len("/spec/") :]
        elif path.startswith("/"):
            path = path[1:]

        parts = path.split("/") if path else []

        if not parts:
            raise AdmissionError(f"Invalid patch path: {patch.get('path')}")

        if op == "add" or op == "replace":
            value = patch.get("value")
            target = result
            for part in parts[:-1]:
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    raise AdmissionError(f"Patch path not found: {patch.get('path')}")
            target[parts[-1]] = value

        elif op == "remove":
            target = result
            for part in parts[:-1]:
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    raise AdmissionError(f"Patch path not found: {patch.get('path')}")
            if isinstance(target, dict) and parts[-1] in target:
                del target[parts[-1]]
            else:
                raise AdmissionError(f"Patch path not found: {patch.get('path')}")

        else:
            raise AdmissionError(f"Unsupported patch operation: {op}")

    return result


class AdmissionChain:
    """
    Orchestrates admission webhook execution.

    Fetches matching webhooks from the database, runs mutating webhooks
    first (accumulating patches), then validating webhooks (stopping on
    first denial).
    """

    def __init__(self, db_manager: Any):
        self._db = db_manager

    async def run(self, request: AdmissionRequest) -> Dict[str, Any]:
        """
        Run the admission chain for a request.

        Args:
            request: The admission request.

        Returns:
            The (potentially mutated) spec after all webhooks have run.

        Raises:
            AdmissionError: If a validating webhook denies the request.
        """
        webhooks = await self._db.get_matching_webhooks(
            resource_type_name=request.resource["resource_type_name"],
            resource_type_version=request.resource["resource_type_version"],
            operation=request.operation,
        )

        if not webhooks:
            return request.resource["spec"]

        mutating = [w for w in webhooks if w["webhook_type"] == "mutating"]
        validating = [w for w in webhooks if w["webhook_type"] == "validating"]

        # Run mutating webhooks, accumulating patches
        for webhook in mutating:
            response = await self._call_webhook(webhook, request)
            if not response.allowed:
                raise AdmissionError(
                    response.message or f"Denied by mutating webhook {webhook['name']}"
                )
            if response.patches:
                request.resource["spec"] = apply_patches(
                    request.resource["spec"], response.patches
                )

        # Run validating webhooks, stopping on first denial
        for webhook in validating:
            response = await self._call_webhook(webhook, request)
            if not response.allowed:
                raise AdmissionError(
                    response.message
                    or f"Denied by validating webhook {webhook['name']}"
                )

        return request.resource["spec"]

    async def _call_webhook(
        self, webhook: Dict[str, Any], request: AdmissionRequest
    ) -> AdmissionResponse:
        """
        Call a single webhook endpoint.

        Args:
            webhook: The webhook configuration dict from the database.
            request: The admission request.

        Returns:
            AdmissionResponse from the webhook.

        Raises:
            AdmissionError: If the call fails and failure_policy is 'Fail'.
        """
        payload = {
            "operation": request.operation,
            "resource": request.resource,
            "old_resource": request.old_resource,
        }

        timeout = aiohttp.ClientTimeout(total=webhook["timeout_seconds"])

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(webhook["webhook_url"], json=payload) as resp:
                    if resp.status >= 500:
                        raise aiohttp.ClientError(
                            f"Webhook returned HTTP {resp.status}"
                        )
                    body = await resp.json()

            return AdmissionResponse(
                allowed=body.get("allowed", False),
                message=body.get("message", ""),
                patches=body.get("patches", []),
            )

        except Exception as e:
            logger.warning(f"Admission webhook {webhook['name']} failed: {e}")
            if webhook["failure_policy"] == "Ignore":
                return AdmissionResponse(allowed=True, message="Webhook error ignored")
            raise AdmissionError(f"Admission webhook {webhook['name']} failed: {e}")
