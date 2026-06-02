"""Unit tests for the citation crawler's pure logic (no Neo4j / no network)."""
import uuid

from augmentation.common.config_reader import AppConfig, DatabaseConfig, OpenAlexConfig, PathsConfig
from augmentation.ingest_scripts.crawl_citations import CitationCrawler, SOURCE, publication_row


def make_config(tmp_path) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(uri="bolt://localhost:7687", user="neo4j", password="x"),
        paths=PathsConfig(openalex_cache_directory=str(tmp_path / "cache"), log_directory=str(tmp_path / "logs")),
        openalex=OpenAlexConfig(email="", requests_per_second=10, batch_size=50),
    )


def crawler(tmp_path):
    return CitationCrawler(config=make_config(tmp_path), client=object())


def test_publication_row_normalizes_doi_and_sets_provenance():
    row = publication_row(
        {"doi": "https://doi.org/10.1/ABC", "title": "T", "publication_year": 2021}, hop=1, global_id="gid-1")
    assert row == {"globalId": "gid-1", "doi": "10.1/abc", "title": "T", "year": "2021",
                   "source": SOURCE, "crawlHop": 1}


def test_publication_row_none_without_doi():
    assert publication_row({"title": "no doi", "publication_year": 2020}, hop=2, global_id="x") is None


def test_resolve_global_id_reuses_existing_uppercase_keyed_pub(tmp_path):
    c = crawler(tmp_path)
    # existing graph keyed globalId on the UPPERCASE doi; index maps normalized->that id
    c._pub_index = {"10.1111/gcb.15824": "EXISTING-GID"}
    assert c.resolve_global_id("10.1111/gcb.15824") == "EXISTING-GID"  # dedupes to existing node


def test_resolve_global_id_mints_uuid5_for_new_pub(tmp_path):
    c = crawler(tmp_path)
    c._pub_index = {}
    norm = "10.5194/new-paper-2024"
    assert c.resolve_global_id(norm) == str(uuid.uuid5(uuid.NAMESPACE_DNS, norm))


def test_rows_from_citing_works_dedups_and_resolves(tmp_path):
    c = crawler(tmp_path)
    c._pub_index = {"10.1/existing": "EXISTING-GID"}
    citing = [
        {"doi": "https://doi.org/10.1/EXISTING", "title": "known", "publication_year": 2019},  # -> existing gid
        {"doi": "https://doi.org/10.1/new", "title": "fresh", "publication_year": 2023},        # -> minted
        {"doi": "https://doi.org/10.1/NEW", "title": "dup of fresh", "publication_year": 2023},  # dedup (same norm)
        {"title": "no doi - skipped"},
    ]
    rows = c.rows_from_citing_works(citing, hop=2)
    assert len(rows) == 2
    by_doi = {r["doi"]: r for r in rows}
    assert by_doi["10.1/existing"]["globalId"] == "EXISTING-GID"
    assert by_doi["10.1/new"]["globalId"] == str(uuid.uuid5(uuid.NAMESPACE_DNS, "10.1/new"))
    assert all(r["crawlHop"] == 2 for r in rows)
