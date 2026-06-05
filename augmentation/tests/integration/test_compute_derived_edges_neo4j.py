"""Integration test: CO_USED_WITH derived edges against a live Neo4j (scoped to throwaway nodes)."""
import uuid

import pytest

from augmentation.common.config_reader import load_config
from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts.compute_derived_edges import DerivedEdgeBuilder

pytestmark = pytest.mark.integration

SUFFIX = uuid.uuid4().hex[:8]
DS = {k: f"itest-ds-{k}-{SUFFIX}" for k in ("A", "B", "C")}
P1, P2 = f"itest-pub1-{SUFFIX}", f"itest-pub2-{SUFFIX}"


@pytest.fixture
def driver():
    try:
        drv = get_driver(load_config())
        drv.verify_connectivity()
    except Exception as e:
        pytest.skip(f"Neo4j not reachable: {e}")
    # seed: P1 uses A,B,C ; P2 uses A,B  -> A-B weight 2, A-C weight 1, B-C weight 1
    with drv.session() as s:
        for gid in DS.values():
            s.run("CREATE (:Dataset {globalId:$id, shortName:'ITEST'})", id=gid)
        s.run("CREATE (:Publication {globalId:$id})", id=P1)
        s.run("CREATE (:Publication {globalId:$id})", id=P2)
        for ds in ("A", "B", "C"):
            s.run("MATCH (p:Publication {globalId:$p}),(d:Dataset {globalId:$d}) MERGE (p)-[:USES_DATASET]->(d)",
                  p=P1, d=DS[ds])
        for ds in ("A", "B"):
            s.run("MATCH (p:Publication {globalId:$p}),(d:Dataset {globalId:$d}) MERGE (p)-[:USES_DATASET]->(d)",
                  p=P2, d=DS[ds])
    yield drv
    with drv.session() as s:
        s.run("MATCH (n) WHERE n.globalId IN $ids DETACH DELETE n", ids=list(DS.values()) + [P1, P2])
    drv.close()


def test_co_used_with_weights_dedup_and_idempotent(driver):
    builder = DerivedEdgeBuilder(config=load_config())
    builder.driver = driver
    scope = list(DS.values())

    builder.compute_co_used_with(dataset_global_ids=scope)

    def edges():
        with driver.session() as s:
            return {
                tuple(sorted([r["a"], r["b"]])): {"w": r["w"], "derived": r["derived"]}
                for r in s.run(
                    "MATCH (d1:Dataset)-[r:CO_USED_WITH]->(d2:Dataset) "
                    "WHERE d1.globalId IN $scope AND d2.globalId IN $scope "
                    "RETURN d1.globalId AS a, d2.globalId AS b, r.weight AS w, r.derived AS derived",
                    scope=scope)
            }

    e = edges()
    assert len(e) == 3                                   # A-B, A-C, B-C — one direction each (no dupes)
    assert e[tuple(sorted([DS["A"], DS["B"]]))]["w"] == 2  # A,B co-used by P1 and P2
    assert e[tuple(sorted([DS["A"], DS["C"]]))]["w"] == 1  # A,C only by P1
    assert all(v["derived"] is True for v in e.values())

    # Idempotent: a second run leaves the same 3 edges with the same weights.
    builder.compute_co_used_with(dataset_global_ids=scope)
    e2 = edges()
    assert len(e2) == 3
    assert e2[tuple(sorted([DS["A"], DS["B"]]))]["w"] == 2
