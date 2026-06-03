"""Unit tests for the daily crawl driver's lock + completeness logic (no Neo4j/network)."""
import augmentation.ingest_scripts.daily_crawl as dc


def test_acquire_lock_blocks_when_held_by_live_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "LOCK_PATH", str(tmp_path / "lock"))
    assert dc._acquire_lock() is True          # first acquire writes our (alive) pid
    assert dc._acquire_lock() is False         # second sees a live pid -> blocked


def test_acquire_lock_reclaims_stale_lock(tmp_path, monkeypatch):
    lock = tmp_path / "lock"
    monkeypatch.setattr(dc, "LOCK_PATH", str(lock))
    lock.write_text("999999")                  # a pid that does not exist
    assert dc._acquire_lock() is True          # stale lock reclaimed


class _Session:
    def __init__(self, counts):
        self._counts = list(counts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **k):
        val = self._counts.pop(0)
        return type("R", (), {"single": lambda self: {"c": val}})()


class _Driver:
    def __init__(self, counts):
        self._counts = counts

    def session(self):
        return _Session(self._counts)


def test_crawl_complete_true_when_nothing_pending():
    assert dc._crawl_complete(_Driver([0, 0])) is True       # 0 datasets, 0 frontier pubs pending


def test_crawl_complete_false_with_pending_datasets():
    assert dc._crawl_complete(_Driver([5])) is False         # pending datasets -> short-circuits
