"""
Action Plugin Base - Abstract interface for infrastructure actions.

Action plugins are optional executors that reconciler plugins can use to
apply changes. The default shipped plugin is GitHub Actions, which triggers
and monitors GitHub Actions workflow runs.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from plugins.base import ActionContext, ActionResult, DriftResult


class ActionPlugin(ABC):
    """
    Abstract base class for action plugins.

    Action plugins are responsible for executing infrastructure changes.
    Each plugin implements a standard interface for prepare, plan, apply,
    and destroy operations.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this plugin (e.g., 'github_actions')."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Plugin version string."""
        pass

    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialize the plugin with configuration.

        Called once when the plugin is loaded. Use this to set up
        any persistent state, connections, or validate configuration.

        Args:
            config: Plugin-specific configuration dictionary
        """
        pass

    @abstractmethod
    async def validate_spec(self, spec: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate that a resource spec is valid for this plugin.

        Args:
            spec: The resource specification to validate

        Returns:
            Tuple of (is_valid, error_message). If valid, error_message is None.
        """
        pass

    @abstractmethod
    async def prepare(self, ctx: ActionContext) -> Any:
        """
        Prepare for execution (e.g., create workspace, download dependencies).

        Args:
            ctx: The action context with resource details

        Returns:
            A workspace handle that will be passed to other methods.
            The type is plugin-specific.
        """
        pass

    @abstractmethod
    async def plan(self, ctx: ActionContext, workspace: Any) -> ActionResult:
        """
        Determine what changes would be made.

        Args:
            ctx: The action context with resource details
            workspace: The workspace handle from prepare()

        Returns:
            ActionResult with plan_output populated and has_changes indicating
            whether changes are needed.
        """
        pass

    @abstractmethod
    async def apply(self, ctx: ActionContext, workspace: Any) -> ActionResult:
        """
        Apply the planned changes.

        Args:
            ctx: The action context with resource details
            workspace: The workspace handle from prepare()

        Returns:
            ActionResult with apply_output and resource counts populated.
        """
        pass

    @abstractmethod
    async def destroy(self, ctx: ActionContext, workspace: Any) -> ActionResult:
        """
        Destroy all resources managed by this spec.

        Args:
            ctx: The action context with resource details
            workspace: The workspace handle from prepare()

        Returns:
            ActionResult indicating success/failure of destruction.
        """
        pass

    @abstractmethod
    async def get_outputs(self, ctx: ActionContext, workspace: Any) -> Dict[str, Any]:
        """
        Get output values from the last apply.

        Args:
            ctx: The action context with resource details
            workspace: The workspace handle from prepare()

        Returns:
            Dictionary of output name to output value.
        """
        pass

    @abstractmethod
    async def get_state(
        self, ctx: ActionContext, workspace: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Get the current state/status of managed resources.

        Args:
            ctx: The action context with resource details
            workspace: The workspace handle from prepare()

        Returns:
            Dictionary containing state information, or None if no state exists.
        """
        pass

    @abstractmethod
    async def cleanup(self, workspace: Any) -> None:
        """
        Clean up workspace and temporary resources.

        Called after reconciliation completes (success or failure).

        Args:
            workspace: The workspace handle from prepare()
        """
        pass

    async def detect_drift(self, ctx: ActionContext, workspace: Any) -> DriftResult:
        """
        Detect drift between desired and actual state.

        This is an optional method with a default implementation that returns
        no drift. Plugins that support drift detection should override this.

        Args:
            ctx: The action context with resource details
            workspace: The workspace handle from prepare()

        Returns:
            DriftResult indicating whether drift was detected and details.
        """
        # Default implementation: no drift detection capability
        return DriftResult(
            has_drift=False, drift_details="Drift detection not supported"
        )

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        """
        Load plugin-specific configuration from environment variables.

        Override this method in subclasses to define how the plugin
        loads its configuration from the environment.

        Returns:
            Dictionary of configuration values for this plugin.
        """
        return {}
