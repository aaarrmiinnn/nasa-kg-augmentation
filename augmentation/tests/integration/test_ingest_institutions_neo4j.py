"""Integration test: Institution + AFFILIATED_WITH ingest against a live Neo4j.

Creates throwaway Author/Institution test nodes, asserts the affiliation edge and
idempotency, then removes all test-created nodes so the base graph is untouched.
"""
import uuid

import pytest

from augmentation.common.config_reader import load_config
from augmentation.common.core import generate_uuid_from_id
from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts.ingest_institutions import InstitutionIngestor, build_affiliation_rows

pytestmark = pytest.mark.integration

A_ID = f"https://openalex.org/Aitest{uuid.uuid4().hex[:8]}"
I_ID = f"https://openalex.org/Iitest{uuid.uuid4().hex[:8]}"


def _work():
    return {"authorships": [
        {"author": {"id": A_ID, "display_name": "Aff Tester", "orcid": None}, "author_position": "first",
         "institutions": [{"id": I_ID, "display_name": "Test Institute", "ror": "https://ror.org/itest",
                           "country_code": "US"}]},
    ]}


@pytest.fixture
def driver():
    config = load_config()
    try:
        drv = get_driver(config)
        drv.verify_connectivity()
    except Exception as e:
        pytest.skip(f"Neo4j not reachable: {e}")
    yield drv
    author_id = generate_uuid_from_id(A_ID)
    institution_id = generate_uuid_from_id(I_ID)
    with drv.session() as s:
        s.run("MATCH (a:Author {globalId: $aid}) DETACH DELETE a", aid=author_id)
        s.run("MATCH (i:Institution {globalId: $iid}) DETACH DELETE i", iid=institution_id)
    drv.close()


def test_affiliated_with_created_and_idempotent(driver):
    ingestor = InstitutionIngestor(client=object())  # client unused in this path
    ingestor.set_uniqueness_constraint()
    author_id = generate_uuid_from_id(A_ID)
    institution_id = generate_uuid_from_id(I_ID)

    rows = build_affiliation_rows(_work())

    with driver.session() as s:
        s.execute_write(ingestor.add_affiliations_batch, rows)
        s.execute_write(ingestor.add_affiliations_batch, rows)  # second write must not duplicate

        rec = s.run(
            "MATCH (a:Author {globalId: $aid})-[r:AFFILIATED_WITH]->(i:Institution {globalId: $iid}) "
            "RETURN count(i) AS insts, count(r) AS edges, i.name AS name, i.ror AS ror, i.country AS country",
            aid=author_id, iid=institution_id,
        ).single()

    assert rec["insts"] == 1
    assert rec["edges"] == 1  # deduped despite two writes
    assert rec["name"] == "Test Institute"
    assert rec["ror"] == "https://ror.org/itest"
    assert rec["country"] == "US"
