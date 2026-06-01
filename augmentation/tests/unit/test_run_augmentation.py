"""Unit test for the coverage report formatter."""
from augmentation.ingest_scripts.run_augmentation import format_coverage


def test_format_coverage_computes_match_percent_and_counts():
    report = format_coverage(
        author_stats={"publications": 200, "matched": 150, "rows": 900},
        institution_stats={"publications": 200, "matched": 150, "rows": 1200},
        graph_counts={"authors": 800, "institutions": 300, "authored_by": 900, "affiliated_with": 1200},
    )
    assert "matched in OpenAlex  : 150 (75.0%)" in report
    assert "Author nodes         : 800" in report
    assert "Institution nodes    : 300" in report
    assert "AUTHORED_BY edges    : 900" in report
    assert "AFFILIATED_WITH edges: 1200" in report


def test_format_coverage_handles_zero_publications():
    report = format_coverage({"publications": 0, "matched": 0}, {}, {})
    assert "(0.0%)" in report
