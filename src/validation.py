"""
Schema Validation - OpenAPI v3 schema validation utilities.

Provides functions to validate resource specs against OpenAPI v3 schemas.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from jsonschema import Draft7Validator, ValidationError

logger = logging.getLogger(__name__)


def validate_openapi_schema(schema: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate that a schema is a valid OpenAPI v3 / JSON Schema.

    Args:
        schema: The schema to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Check that schema is a valid JSON Schema (Draft 7, which OpenAPI 3.0 uses)
        Draft7Validator.check_schema(schema)
        return True, None
    except Exception as e:
        return False, f"Invalid schema: {str(e)}"


def validate_spec_against_schema(
    spec: Dict[str, Any], schema: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """
    Validate a resource spec against an OpenAPI v3 schema.

    Args:
        spec: The resource specification to validate
        schema: The OpenAPI v3 JSON Schema to validate against

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        validator = Draft7Validator(schema, format_checker=Draft7Validator.FORMAT_CHECKER)
        errors = list(validator.iter_errors(spec))

        if not errors:
            return True, None

        # Collect all validation errors
        error_messages = []
        for error in errors:
            path = ".".join(str(p) for p in error.absolute_path) or "(root)"
            error_messages.append(f"{path}: {error.message}")

        return False, "; ".join(error_messages)

    except ValidationError as e:
        return False, f"Validation error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error during validation: {e}")
        return False, f"Validation failed: {str(e)}"
