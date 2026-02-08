"""
Input plugins package.

Input plugins provide mechanisms for users to submit resources
(HTTP API, GitOps, etc.)
"""

from plugins.inputs.base import InputPlugin

__all__ = ["InputPlugin"]
