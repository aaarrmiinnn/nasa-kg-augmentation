"""Integration test: citation crawler hop-1 against a live Neo4j (fake OpenAlex client).

Creates a throwaway Dataset, runs hop-1, asserts USES_DATASET + Publication creation,
the resume checkpoint flag, and idempotency, then removes all test nodes.
"""
import uuid

import pytest

from augmentation.common.config_reader import load_config
from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts.crawl_citations import CitationCrawler

pytestmark = pytest.mark.integration

DS_DOI = f"10.5067/itest-{uuid.uuid4().hex[:8]}"
DS_GID = f"itest-ds-{uuid.uuid4()}"
CITER_DOI = f"10.1/itest-citer-{uuid.uuid4().hex[:8]}"


class _FakeClient:
    rate_limit_remaining = 9999

    def fetch_work_by_doi(self, doi, select=None):
        return {"id": "https://openalex.org/W_itest", "cited_by_count": 1}

    def fetch_citing_works(self, work_id, select=None):
        return [{"id": "https://openalex.org/W_citer", "doi": f"https://doi.org/{CITER_DOI}",
                 "title": "Citing paper", "publication_year": 2023}]


@pytest.fixture
def driver():
    try:
        drv = get_driver(load_config())
        drv.verify_connectivity()
    except Exception as e:
        pytest.skip(f"Neo4j not reachable: {e}")
    yield drv
    citer_gid = str(uuid.uuid5(uuid.NAMESPACE_DNS, CITER_DOI.lower()))
    with drv.session() as s:
        s.run("MATCH (d:Dataset {globalId:$id}) DETACH DELETE d", id=DS_GID)
        s.run("MATCH (p:Publication {globalId:$id}) DETACH DELETE p", id=citer_gid)
    drv.close()


def test_hop1_creates_uses_dataset_and_is_resumable(driver):
    # NOTE: we drive the per-dataset method directly (NOT hop1(), which scans the whole graph)
    # so this test only ever touches its own throwaway dataset.
    with driver.session() as s:
        s.run("CREATE (d:Dataset {globalId:$id, doi:$doi, shortName:'ITEST'})", id=DS_GID, doi=DS_DOI)

    crawler = CitationCrawler(config=load_config(), client=_FakeClient())
    crawler.driver = driver
    crawler.load_publication_index()

    with driver.session() as s:
        written = crawler._process_dataset(s, DS_GID, DS_DOI)
        written_again = crawler._process_dataset(s, DS_GID, DS_DOI)  # idempotent re-run

    assert written == 1
    assert written_again == 1  # fake client always returns 1 citer (idempotency is asserted on edges below)
    with driver.session() as s:
        rec = s.run(
            "MATCH (p:Publication)-[:USES_DATASET]->(d:Dataset {globalId:$id}) "
            "RETURN count(p) AS pubs, count{ (p)-[:USES_DATASET]->(d) } AS edges, "
            "d.citersFetched AS done, collect(p.source)[0] AS source, collect(p.crawlHop)[0] AS hop",
            id=DS_GID).single()
    assert rec["pubs"] == 1
    assert rec["edges"] == 1            # idempotent: one edge despite two writes
    assert rec["done"] is True          # resume checkpoint flag set
    assert rec["source"] == "citation-crawl"
    assert rec["hop"] == 1

    # Resume semantics: hop1()'s frontier query excludes datasets already flagged done.
    with driver.session() as s:
        pending = s.run("MATCH (d:Dataset {globalId:$id}) WHERE d.citersFetched IS NULL RETURN count(d) AS c",
                        id=DS_GID).single()["c"]
    assert pending == 0
