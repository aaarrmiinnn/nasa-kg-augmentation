"""Unit tests for the Author ingest script (no Neo4j needed)."""
from augmentation.common.config_reader import AppConfig, DatabaseConfig, OpenAlexConfig, PathsConfig
from augmentation.common.core import generate_uuid_from_id
from augmentation.ingest_scripts.ingest_authors import AuthorIngestor, build_author_rows


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
        self.calls = []

    def fetch_works_by_dois(self, dois):
        self.calls.append(list(dois))
        return {d: self.works[d] for d in dois if d in self.works}


def test_build_author_rows_maps_authorships_to_edge_rows():
    work = {
        "authorships": [
            {"author": {"id": "https://openalex.org/A1", "display_name": "Ada", "orcid": "https://orcid.org/x"},
             "author_position": "first", "institutions": []},
            {"author": {"id": "https://openalex.org/A2", "display_name": "Bob", "orcid": None},
             "author_position": "last", "institutions": []},
        ]
    }

    rows = build_author_rows("pub-123", work)

    assert [r["name"] for r in rows] == ["Ada", "Bob"]
    assert all(r["pubId"] == "pub-123" for r in rows)
    assert rows[0]["authorId"] == generate_uuid_from_id("https://openalex.org/A1")
    assert rows[0]["authorPosition"] == "first"
    assert rows[0]["orcid"] == "https://orcid.org/x"
    assert rows[1]["orcid"] is None


def test_build_author_rows_skips_authorship_without_author_id():
    work = {"authorships": [
        {"author": {"id": None, "display_name": "Anonymous", "orcid": None}, "author_position": "first", "institutions": []},
    ]}
    assert build_author_rows("pub-123", work) == []


def test_build_author_rows_empty_work():
    assert build_author_rows("pub-123", {}) == []


def test_author_global_id_is_stable_across_papers():
    # Same OpenAlex author in two different works -> same Author.globalId (dedup).
    w1 = {"authorships": [{"author": {"id": "https://openalex.org/A9", "display_name": "X", "orcid": None},
                           "author_position": "first", "institutions": []}]}
    w2 = {"authorships": [{"author": {"id": "https://openalex.org/A9", "display_name": "X", "orcid": None},
                           "author_position": "middle", "institutions": []}]}
    assert build_author_rows("pubA", w1)[0]["authorId"] == build_author_rows("pubB", w2)[0]["authorId"]


def test_process_publications_joins_uppercase_doi_to_lowercase_work(monkeypatch, tmp_path):
    # The publication carries an UPPERCASE DOI; OpenAlex is keyed lowercase.
    # process_publications must normalize before fetching and joining.
    work = {"authorships": [
        {"author": {"id": "https://openalex.org/A1", "display_name": "Ada", "orcid": None},
         "author_position": "first", "institutions": []},
    ]}
    client = _FakeClient({"10.1/abc": work})
    ingestor = AuthorIngestor(config=make_config(tmp_path), client=client)

    captured: list[list[dict]] = []
    ingestor.driver = _FakeDriver(captured)
    monkeypatch.setattr(ingestor, "fetch_publication_dois",
                        lambda limit=None: [{"globalId": "pub-1", "doi": "10.1/ABC"}])

    stats = ingestor.process_publications(batch_size=10)

    assert client.calls == [["10.1/abc"]]  # uppercase normalized to lowercase before the API call
    assert stats == {"publications": 1, "matched": 1, "rows": 1, "failed_batches": 0}
    assert captured == [[{
        "pubId": "pub-1",
        "authorId": generate_uuid_from_id("https://openalex.org/A1"),
        "openalexId": "https://openalex.org/A1",
        "name": "Ada", "orcid": None, "authorPosition": "first",
    }]]


def test_process_publications_skips_unmatched_publication(monkeypatch, tmp_path):
    client = _FakeClient({})  # OpenAlex knows nothing
    ingestor = AuthorIngestor(config=make_config(tmp_path), client=client)
    captured: list[list[dict]] = []
    ingestor.driver = _FakeDriver(captured)
    monkeypatch.setattr(ingestor, "fetch_publication_dois",
                        lambda limit=None: [{"globalId": "pub-1", "doi": "10.9/UNKNOWN"}])

    stats = ingestor.process_publications(batch_size=10)

    assert stats == {"publications": 1, "matched": 0, "rows": 0, "failed_batches": 0}
    assert captured == []  # nothing written
