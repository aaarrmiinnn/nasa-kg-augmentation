"""Unit tests for the Institution / AFFILIATED_WITH ingest (no Neo4j needed)."""
from augmentation.common.config_reader import AppConfig, DatabaseConfig, OpenAlexConfig, PathsConfig
from augmentation.common.core import generate_uuid_from_id
from augmentation.ingest_scripts.ingest_institutions import InstitutionIngestor, build_affiliation_rows


def make_config(tmp_path) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(uri="bolt://localhost:7687", user="neo4j", password="x"),
        paths=PathsConfig(openalex_cache_directory=str(tmp_path / "cache"), log_directory=str(tmp_path / "logs")),
        openalex=OpenAlexConfig(email="", requests_per_second=10, batch_size=50),
    )


class _FakeSession:
    def __init__(self, captured):
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, fn, rows):
        self.captured.append(list(rows))


class _FakeDriver:
    def __init__(self, captured):
        self.captured = captured

    def session(self):
        return _FakeSession(self.captured)


class _FakeClient:
    def __init__(self, works):
        self.works = works

    def fetch_works_by_dois(self, dois):
        return {d: self.works[d] for d in dois if d in self.works}


def _work_two_institutions():
    return {"authorships": [
        {"author": {"id": "https://openalex.org/A1", "display_name": "Dual", "orcid": None},
         "author_position": "first",
         "institutions": [
             {"id": "https://openalex.org/I10", "display_name": "Inst Ten", "ror": "https://ror.org/abc", "country_code": "US"},
             {"id": "https://openalex.org/I20", "display_name": "Inst Twenty", "ror": "https://ror.org/def", "country_code": "DE"},
         ]},
        {"author": {"id": "https://openalex.org/A2", "display_name": "Solo", "orcid": None},
         "author_position": "last", "institutions": []},  # no institutions -> no rows
    ]}


def test_build_affiliation_rows_one_per_author_institution_pair():
    rows = build_affiliation_rows(_work_two_institutions())

    # A1 has two institutions -> 2 rows; A2 has none -> 0 rows.
    assert len(rows) == 2
    assert {r["institutionId"] for r in rows} == {
        generate_uuid_from_id("https://openalex.org/I10"),
        generate_uuid_from_id("https://openalex.org/I20"),
    }
    assert all(r["authorId"] == generate_uuid_from_id("https://openalex.org/A1") for r in rows)
    r10 = next(r for r in rows if r["instOpenalexId"] == "https://openalex.org/I10")
    assert r10["name"] == "Inst Ten"
    assert r10["ror"] == "https://ror.org/abc"
    assert r10["country"] == "US"


def test_build_affiliation_rows_skips_when_author_or_institution_id_missing():
    work = {"authorships": [
        {"author": {"id": None, "display_name": "Anon", "orcid": None}, "author_position": "first",
         "institutions": [{"id": "https://openalex.org/I1", "display_name": "X", "ror": None, "country_code": None}]},
        {"author": {"id": "https://openalex.org/A9", "display_name": "Y", "orcid": None}, "author_position": "first",
         "institutions": [{"id": None, "display_name": "NoId", "ror": None, "country_code": None}]},
    ]}
    assert build_affiliation_rows(work) == []


def test_build_affiliation_rows_dedups_repeated_pair_within_work():
    # Same author + same institution listed twice -> a single row.
    work = {"authorships": [
        {"author": {"id": "https://openalex.org/A1", "display_name": "D", "orcid": None}, "author_position": "first",
         "institutions": [
             {"id": "https://openalex.org/I1", "display_name": "X", "ror": None, "country_code": None},
             {"id": "https://openalex.org/I1", "display_name": "X", "ror": None, "country_code": None},
         ]},
    ]}
    assert len(build_affiliation_rows(work)) == 1


def test_process_publications_builds_affiliations(monkeypatch, tmp_path):
    client = _FakeClient({"10.1/abc": _work_two_institutions()})
    ingestor = InstitutionIngestor(config=make_config(tmp_path), client=client)
    captured: list[list[dict]] = []
    ingestor.driver = _FakeDriver(captured)
    monkeypatch.setattr(ingestor, "fetch_publication_dois",
                        lambda limit=None: [{"globalId": "pub-1", "doi": "10.1/ABC"}])

    stats = ingestor.process_publications(batch_size=10)

    assert stats == {"publications": 1, "matched": 1, "rows": 2, "failed_batches": 0}
    assert len(captured) == 1 and len(captured[0]) == 2
