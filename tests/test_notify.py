"""Unit tests for notify.py send hardening. All network calls are mocked."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import notify  # noqa: E402

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PLAN_STUB = {
    "planStart": "2026-06-15",
    "weeks": [
        {
            "phaseName": "Base",
            "racePace": None,
            "weekRunKm": 17,
            "days": {d: {"label": "Easy 5km", "zone": "Z2"} for d in DAYS},
        }
    ] * 49,
}


class SendTests(unittest.TestCase):
    def test_message_has_high_urgency(self):
        captured = {}
        supabase = mock.MagicMock()
        with mock.patch.object(notify.messaging, "send",
                               side_effect=lambda m: captured.update(msg=m)):
            ok = notify.send(supabase, "tok-alive", "title", "body")
        self.assertTrue(ok)
        self.assertEqual(captured["msg"].webpush.headers["Urgency"], "high")

    def test_dead_token_is_deleted(self):
        supabase = mock.MagicMock()
        err = notify.messaging.UnregisteredError("token gone")
        with mock.patch.object(notify.messaging, "send", side_effect=err):
            ok = notify.send(supabase, "tok-dead", "title", "body")
        self.assertFalse(ok)
        supabase.table.assert_called_with("fcm_tokens")
        supabase.table.return_value.delete.return_value.eq.assert_called_with(
            "token", "tok-dead")

    def test_other_send_errors_do_not_delete(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify.messaging, "send",
                               side_effect=RuntimeError("transient")):
            ok = notify.send(supabase, "tok-x", "title", "body")
        self.assertFalse(ok)
        supabase.table.assert_not_called()


class CountTests(unittest.TestCase):
    def test_daily_reminder_returns_zero_without_tokens(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify, "get_tokens", return_value=[]):
            sent = notify.send_daily_reminder(supabase, PLAN_STUB)
        self.assertEqual(sent, 0)

    def test_daily_reminder_counts_successes(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify, "get_tokens", return_value=["a", "b"]), \
             mock.patch.object(notify, "send", side_effect=[True, False]):
            sent = notify.send_daily_reminder(supabase, PLAN_STUB)
        self.assertEqual(sent, 1)


class AlertTests(unittest.TestCase):
    def test_alert_sends_text_to_all_tokens(self):
        supabase = mock.MagicMock()
        calls = []
        with mock.patch.object(notify, "get_tokens", return_value=["a", "b"]), \
             mock.patch.object(notify, "send",
                               side_effect=lambda s, t, title, body: calls.append((t, title, body)) or True):
            sent = notify.send_alert(supabase, "Garmin sync failed")
        self.assertEqual(sent, 2)
        self.assertEqual(calls[0][2], "Garmin sync failed")
        self.assertIn("alert", calls[0][1].lower())

    def test_alert_returns_zero_without_tokens(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify, "get_tokens", return_value=[]):
            sent = notify.send_alert(supabase, "boom")
        self.assertEqual(sent, 0)


if __name__ == "__main__":
    unittest.main()
