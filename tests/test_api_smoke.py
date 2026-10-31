import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from crowdcount.server.app import app
from crowdcount.state import DEFAULT_GLOBAL_THRESHOLD, shared_state
from crowdcount.zones import ZoneStats


def reset_shared_state() -> None:
    """Reset singleton state between tests to avoid cross-test bleed."""
    shared_state.reset()
    shared_state.set_global_threshold(DEFAULT_GLOBAL_THRESHOLD)
    for zone in shared_state.get_zone_thresholds():
        shared_state.set_zone_threshold(zone, None)
    shared_state._listeners.clear()


class ApiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_shared_state()

    def test_root_health_endpoint(self) -> None:
        with patch("crowdcount.server.app._load_config", return_value={"thresholds": {"global_threshold": 42, "zone_thresholds": {"A": 7}}}):
            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["api"], "People Detection API")
        self.assertEqual(data["version"], "1.0.0")
        self.assertFalse(data["detection_running"])
        self.assertEqual(shared_state.get_global_threshold(), 42)
        self.assertEqual(shared_state.get_zone_thresholds(), {"A": 7})

    def test_count_and_zones_endpoints(self) -> None:
        shared_state.publish(total=3, zones={"Entrance": ZoneStats(current=2, total_visitors=3)}, points=[(100, 200)])

        with patch("crowdcount.server.app._load_config", return_value={}):
            with TestClient(app) as client:
                count_response = client.get("/count")
                zones_response = client.get("/zones")

        self.assertEqual(count_response.status_code, 200)
        count_data = count_response.json()
        self.assertEqual(count_data["total_count"], 3)
        self.assertIsNotNone(count_data["timestamp"])

        self.assertEqual(zones_response.status_code, 200)
        zones_data = zones_response.json()
        self.assertEqual(zones_data["zones"]["Entrance"]["current"], 2)
        self.assertEqual(zones_data["zones"]["Entrance"]["total_visitors"], 3)

    def test_alerts_endpoint_edge_trigger_recording(self) -> None:
        shared_state.set_global_threshold(2)

        with patch("crowdcount.server.app._load_config", return_value={}), patch("crowdcount.server.audit.record_alert") as mock_record:
            with TestClient(app) as client:
                shared_state.publish(total=3, zones={})
                shared_state.publish(total=4, zones={})
                first = client.get("/alerts")
                second = client.get("/alerts")

        self.assertEqual(first.status_code, 200)
        first_data = first.json()
        self.assertTrue(first_data["has_alerts"])
        self.assertIn("global", first_data["alerts"])

        self.assertEqual(second.status_code, 200)
        self.assertEqual(mock_record.call_count, 1)

    def test_export_csv_endpoint_has_expected_columns(self) -> None:
        shared_state.publish(total=1, zones={"A": ZoneStats(1, 1)})
        shared_state.publish(total=2, zones={"A": ZoneStats(2, 2), "B": ZoneStats(1, 1)})

        with patch("crowdcount.server.app._load_config", return_value={}):
            with TestClient(app) as client:
                response = client.get("/export/csv")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        content = response.text
        self.assertIn("timestamp,total_count", content)
        self.assertIn("zone_A", content)
        self.assertIn("zone_B", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
