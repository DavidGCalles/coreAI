import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from pydantic import ValidationError

from src.schemas.memory import (
    DomainType,
    Visibility,
    MemoryMetadata,
    CoreMemoryNode,
)


def test_metadata_creation_with_defaults():
    """Test that MemoryMetadata can be created with defaults."""
    metadata = MemoryMetadata()
    assert metadata.tags == []
    assert metadata.properties == {}
    assert metadata.source_id is None
    assert isinstance(metadata.created_at, str)  # ISO format datetime string
    assert isinstance(metadata.updated_at, str)
def test_metadata_custom_properties():
    """Test that MemoryMetadata accepts custom tags and properties."""
    metadata = MemoryMetadata(
        tags=["finance", "urgent"],
        properties={"risk_level": "high"},
        source_id="doc_123"
    )
    assert metadata.tags == ["finance", "urgent"]
    assert metadata.properties == {"risk_level": "high"}
    assert metadata.source_id == "doc_123"


def test_metadata_timestamp_defaults():
    """Test that timestamps default to current UTC time."""
    import datetime
    metadata = MemoryMetadata()

    # Both created_at and updated_at should be ISO format strings
    assert "T" in metadata.created_at  # ISO 8601 contains T separator
    assert "Z" in metadata.created_at or "+00:00" in metadata.created_at
    assert isinstance(metadata.updated_at, str)


def test_core_memory_node_creation():
    """Test CoreMemoryNode with required fields."""
    node = CoreMemoryNode(
        tenant_id="user42",
        domain=DomainType.DOCUMENT,
        content="This is the vectorizable text."
    )

    assert node.tenant_id == "user42"
    assert node.domain == DomainType.DOCUMENT
    assert node.visibility == Visibility.PRIVATE  # Default visibility
    assert isinstance(node.metadata, MemoryMetadata)


def test_core_memory_node_custom_visibility():
    """Test CoreMemoryNode accepts custom visibility."""
    node = CoreMemoryNode(
        tenant_id="tenant_a",
        domain=DomainType.CONVERSATION,
        content="Conversation history snippet.",
        visibility=Visibility.SHARED
    )

    assert node.visibility == Visibility.SHARED


def test_core_memory_node_with_custom_metadata():
    """Test CoreMemoryNode with custom metadata."""
    base_metadata = MemoryMetadata(
        tags=["important"],
        properties={"version": "1.0"}
    )

    node = CoreMemoryNode(
        tenant_id="tenant_b",
        domain=DomainType.SYSTEM,
        content="System instruction.",
        metadata=base_metadata
    )

    assert node.metadata.tags == ["important"]
    assert node.metadata.properties == {"version": "1.0"}


def test_domain_type_enum_values():
    """Test DomainType enum has expected values."""
    for dt in DomainType:
        if dt.value not in ("document", "conversation", "system"):
            raise AssertionError(f"Unexpected domain type value: {dt.value}")


def test_visibility_enum_values():
    """Test Visibility enum has expected values."""
    for v in Visibility:
        if v.value not in ("private", "shared", "public"):
            raise AssertionError(f"Unexpected visibility value: {v.value}")

