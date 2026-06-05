"""Integration test: WORKS_WITH_DATASET derived edges against a live Neo4j (scoped to throwaway nodes)."""
import uuid

import pytest

from augmentation.common.config_reader import load_config
from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts.compute_derived_edges import DerivedEdgeBuilder

pytestmark = pytest.mark.integration

S = uuid.uuid4().hex[:8]
D = f"itest-ds-{S}"
A = f"itest-author-{S}"
I = f"itest-inst-{S}"
P1, P2 = f"itest-pub1-{S}", f"itest-pub2-{S}"


@pytest.fixture
def driver():
    try:
        drv = get_driver(load_config())
        drv.verify_connectivity()
    except Exception as e:
        pytest.skip(f"Neo4j not reachable: {e}")
    # A authored P1 & P2; both use D; A affiliated with I  -> A->D weight 2, I->D present
    with drv.session() as s:
        s.run("CREATE (:Dataset {globalId:$id, shortName:'ITEST'})", id=D)
        s.run("CREATE (:Author {globalId:$id, name:'IT Author'})", id=A)
        s.run("CREATE (:Institution {globalId:$id, name:'IT Inst'})", id=I)
        s.run("MATCH (a:Author {globalId:$a}),(i:Institution {globalId:$i}) MERGE (a)-[:AFFILIATED_WITH]->(i)", a=A, i=I)
        for p in (P1, P2):
            s.run("CREATE (:Publication {globalId:$id})", id=p)
            s.run("MATCH (p:Publication {globalId:$p}),(a:Author {globalId:$a}) MERGE (p)-[:AUTHORED_BY]->(a)", p=p, a=A)
            s.run("MATCH (p:Publication {globalId:$p}),(d:Dataset {globalId:$d}) MERGE (p)-[:USES_DATASET]->(d)", p=p, d=D)
    yield drv
    with drv.session() as s:
        s.run("MATCH (n) WHERE n.globalId IN $ids DETACH DELETE n", ids=[D, A, I, P1, P2])
    drv.close()


def test_works_with_dataset_author_and_institution(driver):
    builder = DerivedEdgeBuilder(config=load_config())
    builder.driver = driver

    builder.compute_works_with_dataset(dataset_global_ids=[D])

    with driver.session() as s:
        author = s.run(
            "MATCH (a:Author {globalId:$a})-[r:WORKS_WITH_DATASET]->(d:Dataset {globalId:$d}) "
            "RETURN r.weight AS w, r.derived AS derived, count(r) AS edges", a=A, d=D).single()
        inst = s.run(
            "MATCH (i:Institution {globalId:$i})-[r:WORKS_WITH_DATASET]->(d:Dataset {globalId:$d}) "
            "RETURN r.weight AS w, r.derived AS derived, count(r) AS edges", i=I, d=D).single()

    assert author["edges"] == 1 and author["w"] == 2 and author["derived"] is True   # 2 evidencing pubs
    assert inst["edges"] == 1 and inst["w"] == 2 and inst["derived"] is True

    # Idempotent: re-run keeps one edge each, same weights.
    builder.compute_works_with_dataset(dataset_global_ids=[D])
    with driver.session() as s:
        n = s.run("MATCH (x)-[r:WORKS_WITH_DATASET]->(d:Dataset {globalId:$d}) RETURN count(r) AS c", d=D).single()["c"]
    assert n == 2  # exactly one Author->D and one Institution->D
