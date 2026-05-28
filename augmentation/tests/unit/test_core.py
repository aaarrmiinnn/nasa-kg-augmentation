import os
import json
import pytest
from augmentation.common.core import generate_uuid_from_id, generate_uuid_from_doi, find_json_files


class TestGenerateUuid:
    def test_generate_uuid_from_id_deterministic(self):
        id1 = generate_uuid_from_id("A5023888391")
        id2 = generate_uuid_from_id("A5023888391")
        assert id1 == id2

    def test_generate_uuid_from_id_different_inputs(self):
        id1 = generate_uuid_from_id("A5023888391")
        id2 = generate_uuid_from_id("A9999999999")
        assert id1 != id2

    def test_generate_uuid_from_doi_matches_edgraph(self):
        """Verify our UUID generation is identical to edgraph's for the same DOI."""
        import uuid
        doi = "10.1234/test.doi"
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, doi))
        assert generate_uuid_from_doi(doi) == expected

    def test_generate_uuid_from_id_and_doi_same_input(self):
        """Both functions use the same uuid5 approach, so same input = same output."""
        input_str = "10.1234/test"
        assert generate_uuid_from_id(input_str) == generate_uuid_from_doi(input_str)


class TestFindJsonFiles:
    def test_finds_json_files(self, tmp_path):
        (tmp_path / "a.json").write_text("{}")
        (tmp_path / "b.json").write_text("{}")
        (tmp_path / "c.txt").write_text("not json")

        files = list(find_json_files(str(tmp_path)))
        assert len(files) == 2
        assert all(f.endswith(".json") for f in files)

    def test_finds_nested_json_files(self, tmp_path):
        subdir = tmp_path / "nested"
        subdir.mkdir()
        (subdir / "deep.json").write_text("{}")
        (tmp_path / "top.json").write_text("{}")

        files = list(find_json_files(str(tmp_path)))
        assert len(files) == 2

    def test_empty_directory(self, tmp_path):
        files = list(find_json_files(str(tmp_path)))
        assert files == []
