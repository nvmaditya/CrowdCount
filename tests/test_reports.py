import pytest

from crowdcount.server.models import ActivityLog, LogAction, LogCategory
from crowdcount.server.reports import activity_report, summary_report

# Strings that ReportLab's paragraph parser would read as markup if passed through raw.
MARKUP_NAMES = ["Hall <br> East", "<b>Stage", "Food & Drink", "a < b"]


@pytest.mark.parametrize("name", MARKUP_NAMES)
def test_summary_report_treats_zone_names_as_text(name):
    summary = {"total_count": 1, "zones": {name: {"current": 1, "total_visitors": 2, "threshold": 5}}}
    history = [{"timestamp": "2026-11-01T10:00:00", "total_count": 1, "zone_counts": {name: 1}}]
    assert summary_report(summary, history).startswith(b"%PDF")


@pytest.mark.parametrize("name", MARKUP_NAMES)
def test_activity_report_treats_details_as_text(name):
    entry = ActivityLog(
        category=LogCategory.CONFIG,
        action=LogAction.ZONE_ADDED,
        username="admin",
        details=f"Created zone '{name}'",
    )
    assert activity_report([entry], total=1).startswith(b"%PDF")
