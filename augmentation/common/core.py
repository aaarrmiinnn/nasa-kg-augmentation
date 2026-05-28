import os
import uuid
from typing import Generator


def generate_uuid_from_id(identifier: str) -> str:
    """
    Generate a UUID5 from an arbitrary string identifier (OpenAlex ID, DOI, etc.).
    Uses the same NAMESPACE_DNS + uuid5 approach as edgraph for consistency.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, identifier))


def generate_uuid_from_doi(doi: str) -> str:
    """
    Generate a UUID based on a DOI using uuid5.
    Identical to edgraph's implementation for cross-compatibility.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doi))


def find_json_files(directory: str) -> Generator[str, None, None]:
    """Recursively find and yield all JSON files in the specified directory."""
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".json"):
                yield os.path.join(root, file)
