"""Unit tests for the OpenAlex client. HTTP is mocked with `responses`; no real network calls."""
import pytest
import responses

from augmentation.common.config_reader import AppConfig, DatabaseConfig, PathsConfig, OpenAlexConfig
from augmentation.openalex.client import BACKOFF_BASE_SECONDS, OpenAlexClient, normalize_doi, parse_authorships


def make_config(tmp_path) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(uri="bolt://localhost:7687", user="neo4j", password="x"),
        paths=PathsConfig(openalex_cache_directory=str(tmp_path / "cache"), log_directory=str(tmp_path / "logs")),
        openalex=OpenAlexConfig(email="test@example.com", requests_per_second=10, batch_size=50),
    )


WORKS_URL = "https://api.openalex.org/works"


def work(doi_lower: str, n_authors: int = 1) -> dict:
    """Build a minimal OpenAlex work payload keyed by a lowercase doi.org URL."""
    return {
        "id": f"https://openalex.org/W{abs(hash(doi_lower)) % 10**8}",
        "doi": f"https://doi.org/{doi_lower}",
        "authorships": [{"author": {"id": f"https://openalex.org/A{i}", "display_name": f"Author {i}", "orcid": None},
                         "author_position": "first", "institutions": []} for i in range(n_authors)],
    }


def register_works(results: list[dict]) -> None:
    responses.add(responses.GET, WORKS_URL, json={"results": results, "meta": {"count": len(results)}}, status=200)


def register_echo() -> None:
    """Respond to each request with a work for every DOI named in its filter param."""
    import urllib.parse

    def cb(request):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(request.url).query)
        filt = qs.get("filter", [""])[0]
        dois = filt.replace("doi:", "").split("|") if filt else []
        results = [work(d) for d in dois if d]
        return (200, {}, __import__("json").dumps({"results": results, "meta": {"count": len(results)}}))

    responses.add_callback(responses.GET, WORKS_URL, callback=cb)


@responses.activate
def test_empty_doi_list_returns_empty_and_makes_no_request(tmp_path):
    client = OpenAlexClient(make_config(tmp_path))
    result = client.fetch_works_by_dois([])
    assert result == {}
    assert len(responses.calls) == 0


@responses.activate
def test_fetch_normalizes_doi_case_and_keys_by_bare_lowercase_doi(tmp_path):
    # Graph stores UPPERCASE; OpenAlex returns lowercase doi.org URLs.
    register_works([work("10.1111/gcb.15824")])
    client = OpenAlexClient(make_config(tmp_path))

    result = client.fetch_works_by_dois(["10.1111/GCB.15824"])

    assert set(result.keys()) == {"10.1111/gcb.15824"}
    assert result["10.1111/gcb.15824"]["doi"] == "https://doi.org/10.1111/gcb.15824"
    assert len(responses.calls) == 1
    sent = responses.calls[0].request.url
    assert "filter=doi%3A10.1111%2Fgcb.15824" in sent or "filter=doi:10.1111/gcb.15824" in sent
    assert "mailto=test%40example.com" in sent or "mailto=test@example.com" in sent


@responses.activate
def test_batches_more_than_batch_size_into_multiple_requests(tmp_path):
    register_echo()
    config = make_config(tmp_path)
    config.openalex.batch_size = 2
    client = OpenAlexClient(config)

    dois = [f"10.1/x{i}" for i in range(5)]  # 5 DOIs, batch_size 2 -> 3 requests
    result = client.fetch_works_by_dois(dois)

    assert len(responses.calls) == 3
    assert set(result.keys()) == {normalize_doi(d) for d in dois}


@responses.activate
def test_unmatched_doi_is_absent_from_result(tmp_path):
    # OpenAlex returns only the one work it knows; the unknown DOI just drops out.
    register_works([work("10.1/known")])
    client = OpenAlexClient(make_config(tmp_path))

    result = client.fetch_works_by_dois(["10.1/KNOWN", "10.9/unknown"])

    assert set(result.keys()) == {"10.1/known"}


@responses.activate
def test_cache_hit_avoids_http_and_persists_across_instances(tmp_path):
    register_echo()
    config = make_config(tmp_path)

    first = OpenAlexClient(config).fetch_works_by_dois(["10.1/CACHED"])
    assert len(responses.calls) == 1
    assert "10.1/cached" in first

    # A brand-new client with the same cache dir must serve from disk, not HTTP.
    second = OpenAlexClient(config).fetch_works_by_dois(["10.1/cached"])
    assert len(responses.calls) == 1  # unchanged — no new request
    assert second["10.1/cached"]["doi"] == "https://doi.org/10.1/cached"


@responses.activate
def test_partial_cache_only_fetches_missing_dois(tmp_path):
    register_echo()
    config = make_config(tmp_path)
    client = OpenAlexClient(config)

    client.fetch_works_by_dois(["10.1/a"])  # warms cache for a (1 request)
    assert len(responses.calls) == 1

    result = client.fetch_works_by_dois(["10.1/a", "10.1/b"])  # only b is fetched
    assert len(responses.calls) == 2
    assert set(result.keys()) == {"10.1/a", "10.1/b"}
    # the second request must carry only the un-cached DOI
    assert "10.1%2Fb" in responses.calls[1].request.url or "10.1/b" in responses.calls[1].request.url
    assert "10.1%2Fa" not in responses.calls[1].request.url and "doi:10.1/a" not in responses.calls[1].request.url


@responses.activate
def test_throttles_between_requests_at_configured_rate(tmp_path):
    register_echo()
    config = make_config(tmp_path)
    config.openalex.batch_size = 1
    config.openalex.requests_per_second = 10  # -> 0.1s min interval
    slept: list[float] = []
    client = OpenAlexClient(config, sleep=slept.append)

    client.fetch_works_by_dois(["10.1/a", "10.1/b", "10.1/c"])  # 3 requests

    # Throttle applies between requests, not before the first: N-1 sleeps.
    assert len(responses.calls) == 3
    assert slept == [0.1, 0.1]


@responses.activate
def test_no_throttle_sleep_when_everything_cached(tmp_path):
    register_echo()
    config = make_config(tmp_path)
    OpenAlexClient(config).fetch_works_by_dois(["10.1/a"])  # warm cache

    slept: list[float] = []
    OpenAlexClient(config, sleep=slept.append).fetch_works_by_dois(["10.1/a"])
    assert slept == []


def test_parse_authorships_extracts_authors_and_institutions():
    work_payload = {
        "authorships": [
            {  # author with no institutions
                "author": {"id": "https://openalex.org/A0", "display_name": "Solo Author", "orcid": None},
                "author_position": "first",
                "institutions": [],
            },
            {  # author with two institutions
                "author": {"id": "https://openalex.org/A1", "display_name": "Dual Author",
                           "orcid": "https://orcid.org/0000-0002-1234-5678"},
                "author_position": "last",
                "institutions": [
                    {"id": "https://openalex.org/I10", "display_name": "Inst Ten", "ror": "https://ror.org/abc", "country_code": "US"},
                    {"id": "https://openalex.org/I20", "display_name": "Inst Twenty", "ror": "https://ror.org/def", "country_code": "DE"},
                ],
            },
        ]
    }

    records = parse_authorships(work_payload)

    assert len(records) == 2
    first, last = records
    assert first.author.openalex_id == "https://openalex.org/A0"
    assert first.author.display_name == "Solo Author"
    assert first.author.orcid is None
    assert first.author_position == "first"
    assert first.institutions == []

    assert last.author.openalex_id == "https://openalex.org/A1"
    assert last.author.orcid == "https://orcid.org/0000-0002-1234-5678"
    assert [i.openalex_id for i in last.institutions] == ["https://openalex.org/I10", "https://openalex.org/I20"]
    assert last.institutions[0].ror == "https://ror.org/abc"
    assert last.institutions[1].country_code == "DE"


def test_parse_authorships_empty_when_no_authorships():
    assert parse_authorships({}) == []
    assert parse_authorships({"authorships": []}) == []


@responses.activate
def test_unmatched_doi_is_negative_cached_and_not_refetched(tmp_path):
    # A 200 response that omits a requested DOI -> that DOI is a known miss and
    # must not be requested again on a later run.
    register_works([work("10.1/known")])
    config = make_config(tmp_path)

    first = OpenAlexClient(config).fetch_works_by_dois(["10.1/known", "10.9/missing"])
    assert set(first.keys()) == {"10.1/known"}
    assert len(responses.calls) == 1

    # New client, same cache dir: both DOIs are now cached (one hit, one miss) -> no HTTP.
    second = OpenAlexClient(config).fetch_works_by_dois(["10.1/known", "10.9/missing"])
    assert set(second.keys()) == {"10.1/known"}
    assert len(responses.calls) == 1  # unchanged — the miss was not re-fetched


@responses.activate
def test_retries_on_server_error_then_succeeds(tmp_path):
    responses.add(responses.GET, WORKS_URL, status=503)            # 1st attempt fails
    register_works([work("10.1/x")])                                # 2nd attempt succeeds
    config = make_config(tmp_path)
    slept: list[float] = []
    client = OpenAlexClient(config, sleep=slept.append)

    result = client.fetch_works_by_dois(["10.1/x"])

    assert set(result.keys()) == {"10.1/x"}
    assert len(responses.calls) == 2          # retried once
    assert max(slept) >= BACKOFF_BASE_SECONDS  # a real backoff sleep fired


@responses.activate
def test_pathologically_long_doi_does_not_crash_cache(tmp_path):
    # A base-graph publication had a 2KB malformed DOI; quoting it overflowed the
    # filesystem filename limit and aborted the whole run. The cache must absorb it.
    register_works([work("10.1/ok")])
    long_doi = "10.1/" + "x/" * 1500  # ~3KB -> quoted name far exceeds 255 bytes
    client = OpenAlexClient(make_config(tmp_path))

    result = client.fetch_works_by_dois(["10.1/OK", long_doi])  # must not raise

    assert "10.1/ok" in result
    # the long DOI was an unmatched miss -> negative-cached under a hashed, short name
    import os as _os
    names = _os.listdir(client.cache_dir)
    assert all(len(n) <= 255 for n in names)


@responses.activate
def test_does_not_retry_non_retryable_error(tmp_path):
    import requests
    responses.add(responses.GET, WORKS_URL, status=404)  # 404 is not retryable
    client = OpenAlexClient(make_config(tmp_path), sleep=lambda s: None)
    with pytest.raises(requests.HTTPError):
        client.fetch_works_by_dois(["10.1/x"])
    assert len(responses.calls) == 1  # raised immediately, not retried


@responses.activate
def test_raises_after_exhausting_retries(tmp_path):
    import requests
    for _ in range(10):
        responses.add(responses.GET, WORKS_URL, status=500)
    client = OpenAlexClient(make_config(tmp_path), sleep=lambda s: None)
    with pytest.raises(requests.HTTPError):
        client.fetch_works_by_dois(["10.1/x"])


@responses.activate
def test_result_without_doi_is_skipped(tmp_path):
    responses.add(responses.GET, WORKS_URL,
                  json={"results": [{"id": "https://openalex.org/W1"}, work("10.1/has")],
                        "meta": {"count": 2}}, status=200)
    client = OpenAlexClient(make_config(tmp_path))
    result = client.fetch_works_by_dois(["10.1/has", "10.1/nodoi"])
    assert set(result.keys()) == {"10.1/has"}  # the doi-less result is dropped, no crash
