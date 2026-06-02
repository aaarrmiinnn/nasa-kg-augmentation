"""Client for the OpenAlex API, used to augment the NASA KG with authorship data."""
import hashlib
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

from augmentation.common.config_reader import AppConfig

OPENALEX_WORKS_URL = "https://api.openalex.org/works"

HTTP_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0

# On-disk marker for a DOI that OpenAlex confirmed it has no work for (negative cache).
_MISS_MARKER = {"__openalex_miss__": True}


class _Miss:
    """Sentinel returned by the cache for a known, confirmed miss."""


MISS = _Miss()


HTTP_TIMEOUT = (10, 60)  # (connect, read) seconds — avoid hanging the full run on a stalled socket


@dataclass
class AuthorRef:
    openalex_id: Optional[str]
    display_name: Optional[str]
    orcid: Optional[str]


@dataclass
class InstitutionRef:
    openalex_id: Optional[str]
    display_name: Optional[str]
    ror: Optional[str]
    country_code: Optional[str]


@dataclass
class AuthorshipRecord:
    author: AuthorRef
    author_position: str
    institutions: list[InstitutionRef] = field(default_factory=list)


def parse_authorships(work: dict) -> list[AuthorshipRecord]:
    """Turn an OpenAlex work's ``authorships[]`` into structured records.

    Authors with no institutions yield an empty ``institutions`` list; authors
    with several institutions on one paper yield one record carrying all of them.
    """
    records: list[AuthorshipRecord] = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        institutions = [
            InstitutionRef(
                openalex_id=inst.get("id"),
                display_name=inst.get("display_name"),
                ror=inst.get("ror"),
                country_code=inst.get("country_code"),
            )
            for inst in authorship.get("institutions", [])
        ]
        records.append(
            AuthorshipRecord(
                author=AuthorRef(
                    openalex_id=author.get("id"),
                    display_name=author.get("display_name"),
                    orcid=author.get("orcid"),
                ),
                author_position=authorship.get("author_position"),
                institutions=institutions,
            )
        )
    return records


def normalize_doi(doi: str) -> str:
    """Reduce a DOI to its bare, lowercase form for reliable joining.

    The graph stores DOIs uppercase (e.g. ``10.1111/GCB.15824``) while OpenAlex
    returns lowercase ``https://doi.org/...`` URLs. Both collapse to the same key here.
    """
    if not doi:
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


class OpenAlexClient:
    """Fetches OpenAlex works by DOI for the augmentation pipeline."""

    def __init__(self, config: AppConfig, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self.email = config.openalex.email
        self.batch_size = config.openalex.batch_size
        self.cache_dir = config.paths.openalex_cache_directory
        rps = config.openalex.requests_per_second
        self._min_interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._sleep = sleep
        self._made_request = False
        self.rate_limit_remaining: Optional[int] = None  # updated from x-ratelimit-remaining headers
        self.session = requests.Session()

    def fetch_works_by_dois(self, dois: list[str]) -> dict[str, dict]:
        if not dois:
            return {}

        # Deduplicate while preserving order; serve cache hits, fetch only misses.
        normalized = list(dict.fromkeys(normalize_doi(d) for d in dois))
        results: dict[str, dict] = {}
        misses: list[str] = []
        for doi in normalized:
            cached = self._read_cache(doi)
            if cached is MISS:
                continue  # confirmed-not-in-OpenAlex; don't re-fetch
            if cached is not None:
                results[doi] = cached
            else:
                misses.append(doi)

        for start in range(0, len(misses), self.batch_size):
            chunk = misses[start:start + self.batch_size]
            results.update(self._fetch_batch(chunk))
        return results

    # Filesystem filename limit is ~255 bytes. Most DOIs quote to a short, readable name;
    # the rare malformed DOI (e.g. a publication whose doi field is a 2KB concatenated dump)
    # would blow past the limit, so fall back to a fixed-length hash for those only.
    _MAX_CACHE_NAME = 200

    def _cache_path(self, normalized_doi: str) -> str:
        name = urllib.parse.quote(normalized_doi, safe="")
        if len(name) > self._MAX_CACHE_NAME:
            name = hashlib.sha256(normalized_doi.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, name + ".json")

    def _read_cache(self, normalized_doi: str):
        """Return the cached work dict, the ``MISS`` sentinel, or ``None`` if uncached."""
        path = self._cache_path(normalized_doi)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Treat a corrupt/unreadable cache file as uncached so the run self-heals.
                return None
            if isinstance(data, dict) and data.get("__openalex_miss__"):
                return MISS
            return data
        return None

    def _write_cache(self, normalized_doi: str, work: dict) -> None:
        # Caching is an optimization — never let a cache-write error abort ingestion.
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cache_path(normalized_doi), "w") as f:
                json.dump(work, f)
        except OSError:
            pass

    def _fetch_batch(self, normalized_dois: list[str]) -> dict[str, dict]:
        params = {
            "filter": "doi:" + "|".join(normalized_dois),
            "per-page": self.batch_size,
            "select": "id,doi,authorships",
        }
        if self.email:
            params["mailto"] = self.email

        resp = self._get_with_retry(params)

        batch: dict[str, dict] = {}
        for w in resp.json().get("results", []):
            if w.get("doi"):
                norm = normalize_doi(w["doi"])
                batch[norm] = w
                self._write_cache(norm, w)

        # Negative-cache: this was a successful response, so any requested DOI that
        # didn't come back is confirmed absent from OpenAlex — record it so future
        # runs don't re-request it.
        for doi in normalized_dois:
            if doi not in batch:
                self._write_cache(doi, _MISS_MARKER)
        return batch

    def fetch_work_by_doi(self, doi: str, select: Optional[str] = None) -> Optional[dict]:
        """Resolve a single work via the ``/works/doi:`` endpoint.

        Required for NASA dataset DOIs: their works are retrievable here but are NOT in
        OpenAlex's searchable filter index, so ``fetch_works_by_dois`` can't find them.
        Returns the work dict, or None if OpenAlex has no work for the DOI (negative-cached).

        Note: the cache key (``bydoi:<norm>``) does NOT encode ``select`` — the first call's
        field selection is what gets cached. All current callers pass the same ``select``;
        a future caller needing different fields should bypass or extend this cache.
        """
        norm = normalize_doi(doi)
        if not norm:
            return None
        key = "bydoi:" + norm
        cached = self._read_cache(key)
        if cached is MISS:
            return None
        if cached is not None:
            return cached

        url = OPENALEX_WORKS_URL + "/doi:" + urllib.parse.quote(norm)
        params = {}
        if select:
            params["select"] = select
        if self.email:
            params["mailto"] = self.email
        try:
            resp = self._get_with_retry(params, url=url)
        except requests.HTTPError as e:
            if getattr(getattr(e, "response", None), "status_code", None) == 404:
                self._write_cache(key, _MISS_MARKER)  # OpenAlex has no such work
                return None
            raise
        work = resp.json()
        self._write_cache(key, work)
        return work

    def fetch_citing_works(self, work_id: str,
                           select: str = "id,doi,title,publication_year") -> list[dict]:
        """Return all works that cite ``work_id`` (filter=cites:), cursor-paginated and cached."""
        wid = work_id.rsplit("/", 1)[-1]  # accept a full URL or a bare W-id
        key = "cites:" + wid
        cached = self._read_cache(key)
        if cached is not None and cached is not MISS:
            return cached

        results: list[dict] = []
        cursor: Optional[str] = "*"
        while cursor:
            params = {"filter": f"cites:{wid}", "per-page": 200, "cursor": cursor, "select": select}
            if self.email:
                params["mailto"] = self.email
            data = self._get_with_retry(params).json()
            page = data.get("results", [])
            results.extend(page)
            cursor = data.get("meta", {}).get("next_cursor")
            if not page:
                break  # safety: never loop on an empty page
        self._write_cache(key, results)
        return results

    def _get_with_retry(self, params: dict, url: str = OPENALEX_WORKS_URL) -> requests.Response:
        """GET with throttling, retrying transient errors (429/5xx/timeout) with backoff."""
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            if self._made_request and self._min_interval:
                self._sleep(self._min_interval)
            self._made_request = True
            try:
                resp = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
                remaining = resp.headers.get("x-ratelimit-remaining")
                if remaining is not None:
                    try:
                        self.rate_limit_remaining = int(remaining)
                    except ValueError:
                        pass
                resp.raise_for_status()
                return resp
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                retryable = isinstance(e, (requests.Timeout, requests.ConnectionError)) or status in HTTP_RETRY_STATUSES
                if not retryable or attempt == MAX_RETRIES - 1:
                    raise
                last_exc = e
                self._sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))  # exponential backoff
        # Unreachable: the loop above always returns or raises. Guard against a None raise.
        raise last_exc or RuntimeError(f"Exhausted {MAX_RETRIES} retries without a stored exception")
