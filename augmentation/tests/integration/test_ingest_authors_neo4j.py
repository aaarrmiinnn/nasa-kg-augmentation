"""Integration test: Author + AUTHORED_BY ingest against a live Neo4j.

Requires the local Neo4j (the base KG instance) to be running. Creates a throwaway
Publication, writes authors against it, asserts behavior + idempotency, then removes
all test-created nodes so the base graph is left untouched.
"""
import uuid

import pytest

from augmentation.common.config_reader import load_config
from augmentation.common.core import generate_uuid_from_id
from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts.ingest_authors import AuthorIngestor, build_author_rows

pytestmark = pytest.mark.integration

TEST_PUB_ID = f"itest-pub-{uuid.uuid4()}"
TEST_AUTHOR_OPENALEX_ID = f"https://openalex.org/Aitest{uuid.uuid4().hex[:8]}"


def _work():
    return {
        "doi": "https://doi.org/10.test/integration",
        "authorships": [
            {"author": {"id": TEST_AUTHOR_OPENALEX_ID, "display_name": "Integration Tester",
                        "orcid": "https://orcid.org/0000-0000-0000-0001"},
             "author_position": "first", "institutions": []},
        ],
    }


@pytest.fixture
def driver():
    config = load_config()
    try:
        drv = get_driver(config)
        drv.verify_connectivity()
    except Exception as e:  # Neo4j not available -> skip rather than fail
        pytest.skip(f"Neo4j not reachable: {e}")
    yield drv
    # Cleanup: remove only the test-created nodes.
    author_id = generate_uuid_from_id(TEST_AUTHOR_OPENALEX_ID)
    with drv.session() as s:
        s.run("MATCH (p:Publication {globalId: $pid}) DETACH DELETE p", pid=TEST_PUB_ID)
        s.run("MATCH (a:Author {globalId: $aid}) DETACH DELETE a", aid=author_id)
    drv.close()


def test_authored_by_created_and_idempotent(driver):
    ingestor = AuthorIngestor(client=object())  # client unused in this path
    ingestor.set_uniqueness_constraint()
    author_id = generate_uuid_from_id(TEST_AUTHOR_OPENALEX_ID)

    with driver.session() as s:
        s.run("CREATE (p:Publication {globalId: $pid, doi: '10.test/INTEGRATION', title: 'itest'})", pid=TEST_PUB_ID)

    rows = build_author_rows(TEST_PUB_ID, _work())

    with driver.session() as s:
        s.execute_write(ingestor.add_authors_batch, rows)
        s.execute_write(ingestor.add_authors_batch, rows)  # second write must not duplicate

        rec = s.run(
            "MATCH (p:Publication {globalId: $pid})-[r:AUTHORED_BY]->(a:Author {globalId: $aid}) "
            "RETURN count(a) AS authors, count(r) AS edges, a.name AS name, a.orcid AS orcid, "
            "r.authorPosition AS pos",
            pid=TEST_PUB_ID, aid=author_id,
        ).single()

    assert rec["authors"] == 1
    assert rec["edges"] == 1  # idempotent: one edge despite two writes
    assert rec["name"] == "Integration Tester"
    assert rec["orcid"] == "https://orcid.org/0000-0000-0000-0001"
    assert rec["pos"] == "first"
