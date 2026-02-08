"""
Action plugins package.

Action plugins implement the actual infrastructure changes (Terraform, Ansible, etc.)
"""

from plugins.actions.base import ActionPlugin

__all__ = ["ActionPlugin"]
