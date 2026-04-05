import unittest
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import alertbot

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "alert_examples")


class TestAlertTypeClassification(unittest.TestCase):
    """Test get_alert_type detection for all supported webhook formats."""

    examples = [
        ("grafana_alert.json", "grafana-alert"),
        ("grafana_resolved.json", "grafana-resolved"),
        ("uptime-kuma-503-alert.json", "uptime-kuma-alert"),
        ("prometheus_alert.json", "alertmanager-alert"),
        ("slack-webhook.json", "slack-webhook"),
    ]

    def test_classification(self):
        for filename, expected_type in self.examples:
            with self.subTest(filename=filename):
                with open(os.path.join(EXAMPLES_DIR, filename)) as f:
                    data = json.load(f)
                self.assertEqual(alertbot.get_alert_type(data), expected_type)

    def test_alertmanager_resolved(self):
        with open(os.path.join(EXAMPLES_DIR, "prometheus_alert.json")) as f:
            data = json.load(f)
        data["status"] = "resolved"
        data["alerts"][0]["status"] = "resolved"
        self.assertEqual(alertbot.get_alert_type(data), "alertmanager-resolved")

    def test_alertmanager_without_job_label(self):
        """Alertmanager alerts that don't originate from Prometheus have no job label."""
        data = {
            "status": "firing",
            "alerts": [{"labels": {"alertname": "CustomAlert", "severity": "warning"}}],
        }
        self.assertEqual(alertbot.get_alert_type(data), "alertmanager-alert")

    def test_not_found(self):
        self.assertEqual(alertbot.get_alert_type({}), "not-found")
        self.assertEqual(alertbot.get_alert_type({"random": "data"}), "not-found")

    def test_malformed_payloads(self):
        """Malformed data should return not-found, not raise."""
        cases = [
            {"alerts": []},
            {"alerts": [{"labels": {}}]},
            {"alerts": "not a list"},
            {"alerts": [None]},
            {"heartbeat": "not a dict"},
        ]
        for data in cases:
            with self.subTest(data=data):
                result = alertbot.get_alert_type(data)
                self.assertIsInstance(result, str)


class TestAlertmanagerToMarkdown(unittest.TestCase):
    """Test the rich Alertmanager formatter."""

    def _make_payload(self, status="firing", alertname="TestAlert",
                      severity="warning", annotations=None, labels=None,
                      starts_at="2026-03-30T08:00:00Z",
                      ends_at="0001-01-01T00:00:00Z",
                      generator_url="", external_url=""):
        alert_labels = {"alertname": alertname, "severity": severity}
        if labels:
            alert_labels.update(labels)
        return {
            "status": status,
            "alerts": [{
                "status": status,
                "labels": alert_labels,
                "annotations": annotations or {},
                "startsAt": starts_at,
                "endsAt": ends_at,
                "generatorURL": generator_url,
            }],
            "externalURL": external_url,
        }

    def test_firing_alert_basic(self):
        payload = self._make_payload(
            annotations={"description": "Something is on fire"},
        )
        messages = alertbot.alertmanager_to_markdown(payload)
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertIn("\U0001f525", msg)
        self.assertIn("**TestAlert**", msg)
        self.assertIn("warning", msg)
        self.assertIn("Something is on fire", msg)

    def test_resolved_alert_shows_duration(self):
        payload = self._make_payload(
            status="resolved",
            starts_at="2026-03-30T08:00:00Z",
            ends_at="2026-03-30T10:15:00Z",
        )
        messages = alertbot.alertmanager_to_markdown(payload)
        msg = messages[0]
        self.assertIn("\u2705", msg)
        self.assertIn("resolved", msg)
        self.assertIn("2h 15m", msg)

    def test_description_preferred_over_summary(self):
        payload = self._make_payload(
            annotations={"description": "Detailed info", "summary": "Short info"},
        )
        msg = alertbot.alertmanager_to_markdown(payload)[0]
        self.assertIn("Detailed info", msg)

    def test_summary_fallback(self):
        payload = self._make_payload(
            annotations={"summary": "Short info"},
        )
        msg = alertbot.alertmanager_to_markdown(payload)[0]
        self.assertIn("Short info", msg)

    def test_extra_labels_shown(self):
        payload = self._make_payload(
            labels={"instance": "server-01:9090", "job": "node"},
        )
        msg = alertbot.alertmanager_to_markdown(payload)[0]
        self.assertIn("server-01:9090", msg)
        self.assertIn("node", msg)
        # alertname and severity should NOT appear in metadata
        self.assertNotIn("\u2022 **Alertname", msg)
        self.assertNotIn("\u2022 **Severity", msg)

    def test_silence_url(self):
        payload = self._make_payload(
            external_url="https://alertmanager.example.com",
        )
        msg = alertbot.alertmanager_to_markdown(payload)[0]
        self.assertIn("[Silence]", msg)
        self.assertIn("alertmanager.example.com", msg)

    def test_no_silence_url_when_resolved(self):
        payload = self._make_payload(
            status="resolved",
            external_url="https://alertmanager.example.com",
            ends_at="2026-03-30T10:00:00Z",
        )
        msg = alertbot.alertmanager_to_markdown(payload)[0]
        self.assertNotIn("[Silence]", msg)

    def test_source_link(self):
        payload = self._make_payload(
            generator_url="http://prometheus:9090/graph?g0.expr=up",
        )
        msg = alertbot.alertmanager_to_markdown(payload)[0]
        self.assertIn("[Source]", msg)

    def test_multiple_alerts_in_group(self):
        payload = self._make_payload()
        payload["alerts"].append({
            "status": "firing",
            "labels": {"alertname": "SecondAlert", "severity": "critical"},
            "annotations": {},
            "startsAt": "2026-03-30T09:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "",
        })
        messages = alertbot.alertmanager_to_markdown(payload)
        self.assertEqual(len(messages), 2)
        self.assertIn("TestAlert", messages[0])
        self.assertIn("SecondAlert", messages[1])

    def test_real_prometheus_example(self):
        with open(os.path.join(EXAMPLES_DIR, "prometheus_alert.json")) as f:
            data = json.load(f)
        messages = alertbot.alertmanager_to_markdown(data)
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertIn("InstanceDown", msg)
        self.assertIn("critical", msg)
        self.assertIn("webserver.example.com", msg)


class TestFormatDuration(unittest.TestCase):

    def test_hours_and_minutes(self):
        self.assertEqual(
            alertbot._format_duration("2026-03-30T08:00:00Z", "2026-03-30T10:15:00Z"),
            "2h 15m",
        )

    def test_days(self):
        result = alertbot._format_duration("2026-03-30T00:00:00Z", "2026-04-01T02:30:00Z")
        self.assertEqual(result, "2d 2h 30m")

    def test_zero_duration(self):
        self.assertEqual(
            alertbot._format_duration("2026-03-30T08:00:00Z", "2026-03-30T08:00:00Z"),
            "0m",
        )

    def test_negative_duration(self):
        self.assertIsNone(
            alertbot._format_duration("2026-03-30T10:00:00Z", "2026-03-30T08:00:00Z")
        )

    def test_invalid_input(self):
        self.assertIsNone(alertbot._format_duration("not-a-date", "also-not"))


class TestFormatTimestamp(unittest.TestCase):

    def test_iso_format(self):
        self.assertEqual(
            alertbot._format_timestamp("2026-03-30T08:00:00Z"),
            "2026-03-30 08:00 UTC",
        )

    def test_invalid_returns_input(self):
        self.assertEqual(alertbot._format_timestamp("garbage"), "garbage")


class TestBuildSilenceUrl(unittest.TestCase):

    def test_basic(self):
        url = alertbot._build_silence_url("https://am.example.com", "HighCPU")
        self.assertIn("am.example.com", url)
        self.assertIn("silences/new", url)
        self.assertIn("HighCPU", url)

    def test_trailing_slash_stripped(self):
        url = alertbot._build_silence_url("https://am.example.com/", "Test")
        self.assertNotIn("//", url.split("://", 1)[1])

    def test_no_external_url(self):
        self.assertIsNone(alertbot._build_silence_url("", "Test"))
        self.assertIsNone(alertbot._build_silence_url(None, "Test"))


if __name__ == "__main__":
    unittest.main()
