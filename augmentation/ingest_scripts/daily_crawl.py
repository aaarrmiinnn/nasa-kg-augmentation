"""Daily auto-resume driver for the citation crawl, intended to run once per day (launchd/cron).

Each run resumes the citation crawl from its graph checkpoint, consumes that day's OpenAlex
quota, and stops gracefully. Once the crawl is fully complete (no pending datasets/frontier),
it auto-runs the author/institution augmentation over the newly-discovered publications — but
only when enough daily quota remains, otherwise it defers to the next day.

Single-instance: a PID lock prevents overlapping runs (stale locks from dead PIDs are reclaimed).
Does NOT publish to Hugging Face.
"""
import os
import sys

from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts import run_augmentation
from augmentation.ingest_scripts.compute_derived_edges import DerivedEdgeBuilder
from augmentation.ingest_scripts.crawl_citations import CitationCrawler

LOCK_PATH = "/tmp/nasakg_daily_crawl.lock"
AUGMENT_MIN_QUOTA = 2000  # only start augmentation when at least this much daily quota remains


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True  # exists but owned by another user


def _acquire_lock() -> bool:
    if os.path.exists(LOCK_PATH):
        try:
            pid = int(open(LOCK_PATH).read().strip() or "0")
        except (ValueError, OSError):
            pid = 0
        if pid and _pid_alive(pid):
            print(f"daily_crawl: another run is active (pid {pid}); exiting.")
            return False
        print("daily_crawl: reclaiming stale lock.")
    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


def _crawl_complete(driver) -> bool:
    with driver.session() as session:
        pending_ds = session.run(
            "MATCH (d:Dataset) WHERE d.doi IS NOT NULL AND d.doi <> '' AND d.doi <> 'N/A' "
            "AND d.citersFetched IS NULL RETURN count(d) AS c"
        ).single()["c"]
        if pending_ds:
            return False
        pending_pubs = session.run(
            "MATCH (p:Publication)-[:USES_DATASET]->(:Dataset) "
            "WHERE p.doi IS NOT NULL AND p.doi <> '' AND p.citersFetched IS NULL RETURN count(p) AS c"
        ).single()["c"]
        return pending_pubs == 0


def main() -> int:
    if not _acquire_lock():
        return 0
    try:
        crawler = CitationCrawler()
        result = crawler.crawl()
        print(f"daily_crawl: crawl result: {result}")

        if _crawl_complete(crawler.driver):
            remaining = crawler.client.rate_limit_remaining
            if remaining is None or remaining > AUGMENT_MIN_QUOTA:
                print("daily_crawl: crawl complete -> running author/institution augmentation over new pubs.")
                print(run_augmentation.run(batch_size=50))
            else:
                print(f"daily_crawl: crawl complete but low quota ({remaining}); deferring augmentation to next run.")
        else:
            print("daily_crawl: crawl not yet complete; will resume on the next run.")

        # Final stage: (re)build derived edges from the current graph. Pure in-DB, no API quota,
        # idempotent — keeps derived edges fresh as the graph grows.
        print("daily_crawl: refreshing derived edges...")
        print(f"daily_crawl: derived edges -> {DerivedEdgeBuilder().build_all()}")
        return 0
    finally:
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
