"""Unit tests for validation.py - OpenAPI v3 schema validation."""

from validation import validate_openapi_schema, validate_spec_against_schema


class TestValidateOpenAPISchema:
    """Tests for validate_openapi_schema function."""

    def test_valid_simple_schema(self):
        """Test validation of a simple valid schema."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None

    def test_valid_schema_with_required(self):
        """Test validation of schema with required fields."""
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "optional": {"type": "boolean"},
            },
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None

    def test_valid_schema_with_nested_objects(self):
        """Test validation of schema with nested objects."""
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "values": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                }
            },
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None

    def test_valid_schema_with_additional_properties(self):
        """Test validation of schema with additionalProperties."""
        schema = {
            "type": "object",
            "additionalProperties": {"type": "string"},
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None

    def test_invalid_schema_bad_type(self):
        """Test that invalid type value is rejected."""
        schema = {
            "type": "invalid_type",
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is False
        assert error is not None
        assert "Invalid schema" in error

    def test_empty_schema_is_valid(self):
        """Test that empty schema is valid (matches anything)."""
        schema = {}
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None

    def test_valid_schema_with_enum(self):
        """Test validation of schema with enum."""
        schema = {
            "type": "string",
            "enum": ["pending", "running", "completed"],
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None

    def test_valid_schema_with_pattern(self):
        """Test validation of schema with pattern."""
        schema = {
            "type": "string",
            "pattern": "^[a-z]+$",
        }
        is_valid, error = validate_openapi_schema(schema)
        assert is_valid is True
        assert error is None


class TestValidateSpecAgainstSchema:
    """Tests for validate_spec_against_schema function."""

    def test_valid_spec_matches_schema(self):
        """Test that valid spec passes validation."""
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
        }
        spec = {"name": "test", "count": 5}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None

    def test_missing_required_field(self):
        """Test that missing required field fails validation."""
        schema = {
            "type": "object",
            "required": ["name", "repo"],
            "properties": {
                "name": {"type": "string"},
                "repo": {"type": "string"},
            },
        }
        spec = {"name": "test"}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None
        assert "repo" in error

    def test_wrong_type(self):
        """Test that wrong type fails validation."""
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
        }
        spec = {"count": "not an integer"}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None

    def test_nested_object_validation(self):
        """Test validation of nested objects."""
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "required": ["enabled"],
                    "properties": {
                        "enabled": {"type": "boolean"},
                    },
                }
            },
        }
        spec = {"config": {"enabled": True}}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None

    def test_nested_object_validation_fails(self):
        """Test that nested object validation catches errors."""
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "required": ["enabled"],
                    "properties": {
                        "enabled": {"type": "boolean"},
                    },
                }
            },
        }
        spec = {"config": {"enabled": "not a boolean"}}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None

    def test_array_validation(self):
        """Test validation of arrays."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
        }
        spec = {"tags": ["tag1", "tag2", "tag3"]}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None

    def test_array_validation_wrong_item_type(self):
        """Test that array with wrong item type fails validation."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
        }
        spec = {"tags": ["tag1", 123, "tag3"]}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None

    def test_enum_validation(self):
        """Test validation with enum constraint."""
        schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "completed"],
                }
            },
        }
        spec = {"status": "running"}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None

    def test_enum_validation_invalid_value(self):
        """Test that invalid enum value fails validation."""
        schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "completed"],
                }
            },
        }
        spec = {"status": "invalid"}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None

    def test_additional_properties_allowed(self):
        """Test that additional properties are allowed by default."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }
        spec = {"name": "test", "extra": "allowed"}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None

    def test_additional_properties_not_allowed(self):
        """Test that additional properties can be disallowed."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "additionalProperties": False,
        }
        spec = {"name": "test", "extra": "not allowed"}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None

    def test_multiple_errors(self):
        """Test that multiple validation errors are reported."""
        schema = {
            "type": "object",
            "required": ["name", "count"],
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
        }
        spec = {}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is False
        assert error is not None
        # Both missing required fields should be mentioned
        assert "name" in error or "count" in error

    def test_empty_spec_against_empty_schema(self):
        """Test empty spec against empty schema."""
        schema = {}
        spec = {}
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None

    def test_github_workflow_schema(self):
        """Test a realistic GitHub workflow schema."""
        schema = {
            "type": "object",
            "required": ["owner", "repo", "workflow"],
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "workflow": {"type": "string"},
                "ref": {"type": "string", "default": "main"},
                "inputs": {"type": "object", "additionalProperties": True},
            },
        }
        spec = {
            "owner": "myorg",
            "repo": "myapp",
            "workflow": "deploy.yml",
            "ref": "main",
            "inputs": {"environment": "production"},
        }
        is_valid, error = validate_spec_against_schema(spec, schema)
        assert is_valid is True
        assert error is None
