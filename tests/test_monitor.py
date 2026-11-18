import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch
from urllib.error import HTTPError

import main

NOW = datetime(2026, 11, 9, 10, tzinfo=main.EASTERN)
ENV = {
    "STOCK_SYMBOLS": "aapl AAPL msft",
    "PERCENT_CHANGE_THRESHOLD": ".10",
    "NTFY_TOPIC": "test-topic",
}


def quote(percent=10.0, when=NOW, phase="regular"):
    prefix = main.PHASES[phase][2]
    return {
        prefix + "ChangePercent": percent,
        prefix + "Price": 11.0,
        prefix + "Time": int(when.timestamp()),
    }


class ConfigTests(unittest.TestCase):
    def parse(self, overrides=None, argv=None):
        with patch.dict(os.environ, {**ENV, **(overrides or {})}, clear=True):
            return main.parse_args(argv or [])

    def test_env_config_normalizes_and_deduplicates_symbols(self):
        args = self.parse()
        self.assertEqual(args.stock_symbols, ["AAPL", "MSFT"])
        self.assertEqual(args.percent_change_threshold, 0.10)
        self.assertEqual(args.ntfy_topic, "test-topic")

    def test_cli_overrides_environment(self):
        self.assertEqual(
            self.parse(
                argv=["--percent_change_threshold", ".05"]
            ).percent_change_threshold,
            0.05,
        )

    def test_invalid_configuration_fails_before_any_network_access(self):
        cases = [
            {"PERCENT_CHANGE_THRESHOLD": value}
            for value in ["0", "-.1", "nan", "inf", "oops"]
        ]
        cases += [
            {"NTFY_TOPIC": ""},
            {"NTFY_TOPIC": "wrong/topic"},
            {"NTFY_TOPIC": "x" * 65},
            {"STOCK_SYMBOLS": "A B C D E F"},
            {"STOCK_SYMBOLS": "a;whoami"},
        ]
        for case in cases:
            with (
                self.subTest(case=case),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                self.parse(case)


class QuoteTests(unittest.TestCase):
    def test_session_boundaries(self):
        for hour, minute, expected in [
            (3, 59, None),
            (4, 0, "pre"),
            (9, 29, "pre"),
            (9, 30, "regular"),
            (15, 59, "regular"),
            (16, 0, "post"),
            (19, 59, "post"),
            (20, 0, None),
        ]:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(
                    main.market_phase(NOW.replace(hour=hour, minute=minute)), expected
                )

    def test_weekday_and_dst_use_eastern_time(self):
        self.assertIsNone(
            main.market_phase(datetime(2026, 11, 7, 10, tzinfo=main.EASTERN))
        )
        self.assertIsNone(main.market_phase(datetime(2026, 11, 9, 0, 30, tzinfo=UTC)))
        self.assertEqual(
            main.market_phase(datetime(2026, 11, 10, 0, 30, tzinfo=UTC)), "post"
        )
        for month, day, utc_hour in [(7, 6, 8), (11, 9, 9)]:
            self.assertEqual(
                main.market_phase(datetime(2026, month, day, utc_hour, tzinfo=UTC)),
                "pre",
            )

    def test_threshold_inclusive_in_both_directions_and_all_sessions(self):
        for phase, hour in [("pre", 8), ("regular", 10), ("post", 17)]:
            now = NOW.replace(hour=hour)
            for change, direction in [(0.10, "UP"), (-0.10, "DOWN")]:
                alert = main.quote_alert(
                    quote(change * 100, now, phase), phase, 0.10, now
                )
                self.assertEqual(alert[0], direction)
                self.assertIn(f"{change:+.2%}", alert[1])
                self.assertIsNone(
                    main.quote_alert(quote(change * 50, now, phase), phase, 0.10, now)
                )

    def test_rejects_old_wrong_session_and_future_quotes(self):
        for when in [
            NOW - timedelta(days=3),
            NOW.replace(hour=9, minute=29),
            NOW + timedelta(minutes=6),
        ]:
            self.assertIsNone(main.quote_alert(quote(when=when), "regular", 0.10, NOW))

    def test_invalid_response_and_values_are_errors(self):
        for invalid in [
            None,
            "Invalid symbol",
            {},
            {"error": "Rate limited"},
            quote(float("nan")),
            quote(float("inf")),
            quote(True),
            {"regularMarketPrice": 0},
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(main.QuoteError):
                main.quote_alert(invalid, "regular", 0.10, NOW)

    def test_absent_extended_hours_trade_is_normal(self):
        self.assertIsNone(main.quote_alert(quote(), "pre", 0.10, NOW.replace(hour=8)))


class YahooTests(unittest.TestCase):
    @patch("main.Ticker")
    def test_reconnects_and_reuses_new_batch_session(self, ticker):
        expired, renewed = Mock(), Mock()
        error = OSError("URL with secret crumb")
        error.response = SimpleNamespace(status_code=401)
        type(expired).quotes = PropertyMock(side_effect=error)
        renewed.quotes = {"SLS": quote(156.359), "CAPR": quote(1.3253)}
        ticker.side_effect = [expired, renewed]
        source = main.YahooQuotes(["SLS", "CAPR"])
        with self.assertLogs("main", level="WARNING") as logs:
            quotes = source.fetch()
        self.assertIn("HTTP 401", logs.output[0])
        self.assertNotIn("secret crumb", logs.output[0])
        self.assertIn(
            "+156.36%", main.quote_alert(quotes["SLS"], "regular", 0.15, NOW)[1]
        )
        self.assertIsNone(main.quote_alert(quotes["CAPR"], "regular", 0.15, NOW))
        self.assertEqual(source.fetch(), quotes)
        self.assertEqual(ticker.call_count, 2)
        ticker.assert_called_with(["SLS", "CAPR"], timeout=15, raise_for_status=True)
        expired.session.close.assert_called_once()
        source.close()
        renewed.session.close.assert_called_once()

    @patch("main.Ticker")
    def test_transient_setup_failure_recovers_within_poll(self, ticker):
        ticker.side_effect = [TimeoutError(), SimpleNamespace(quotes={"SLS": quote()})]
        self.assertIn("SLS", main.YahooQuotes(["SLS"]).fetch())

    @patch("main.Ticker")
    def test_rate_limit_defers_new_requests(self, ticker):
        error = OSError("secret URL")
        error.response = SimpleNamespace(
            status_code=429, headers={"Retry-After": "120"}
        )
        type(ticker.return_value).quotes = PropertyMock(side_effect=error)
        source = main.YahooQuotes(["SLS"])
        with self.assertRaisesRegex(main.QuoteError, "HTTP 429"):
            source.fetch()
        self.assertEqual(source.retry_after, 120)
        ticker.assert_called_once()

    def test_retry_after_accepts_http_dates_and_ignores_invalid_headers(self):
        with patch("main.time.time", return_value=NOW.timestamp()):
            self.assertEqual(
                main.retry_after_seconds("Mon, 09 Nov 2026 15:02:00 GMT"), 120
            )
            self.assertEqual(main.retry_after_seconds("invalid"), 0)

    @patch("main.Ticker")
    def test_malformed_batches_have_bounded_retries(self, ticker):
        ticker.return_value.quotes = "Invalid Crumb"
        with self.assertRaisesRegex(main.QuoteError, "Yahoo batch"):
            main.YahooQuotes(["SLS"]).fetch()
        self.assertEqual(ticker.call_count, 2)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "alert-state.json"
        with patch.dict(os.environ, ENV, clear=True):
            self.args = main.parse_args([])
        self.args.state_file = self.path
        self.args.stock_symbols = ["AAPL"]
        self.source = Mock()
        self.source.fetch.return_value = {"AAPL": quote()}
        self.state = main.AlertState(self.path)
        self.monitor = main.Monitor(self.args, self.state, self.source)
        self.send = Mock()
        self.patcher = patch("main.publish_ntfy", self.send)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_success_suppressed_across_poll_and_process_restart(self):
        self.assertFalse(self.monitor.check(NOW))
        self.assertFalse(self.monitor.check(NOW))
        restarted = main.Monitor(self.args, main.AlertState(self.path), self.source)
        self.assertFalse(restarted.check(NOW))
        self.assertEqual(self.send.call_count, 1)
        contents = self.path.read_text()
        self.assertNotIn(self.args.ntfy_topic, contents)
        self.assertEqual(len(json.loads(contents)["sent"]), 1)

    def test_existing_state_does_not_suppress_first_push_to_new_topic(self):
        self.path.write_text(
            json.dumps({"day": NOW.date().isoformat(), "sent": ["a" * 64]})
        )
        monitor = main.Monitor(self.args, main.AlertState(self.path), self.source)
        self.assertFalse(monitor.check(NOW))
        self.send.assert_called_once()

    def test_failed_publish_retries_original_alert_after_retracement(self):
        self.send.side_effect = [OSError("temporary outage"), None]
        self.assertTrue(self.monitor.check(NOW))
        # Retry the original UP alert even after the price retraces below the threshold.
        self.source.fetch.return_value = {"AAPL": quote(1)}
        self.assertFalse(self.monitor.check(NOW))
        first, retry = self.send.call_args_list
        self.assertEqual(first, retry)
        self.assertIn("+10.00%", retry.args[2])
        self.assertEqual(len(self.state.sent), 1)

    def test_pending_send_retried_during_quote_outage(self):
        self.send.side_effect = [TimeoutError(), None]
        self.assertTrue(self.monitor.check(NOW))
        self.source.fetch.side_effect = main.QuoteError("Yahoo batch failed")
        with self.assertLogs("main", level="ERROR") as logs:
            self.assertTrue(self.monitor.check(NOW))
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(len(self.state.sent), 1)

    def test_reversal_and_next_day_are_new_alerts(self):
        self.monitor.check(NOW)
        self.source.fetch.return_value = {"AAPL": quote(-10)}
        self.monitor.check(NOW)
        tomorrow = NOW + timedelta(days=1)
        self.source.fetch.return_value = {"AAPL": quote(-10, tomorrow)}
        self.monitor.check(tomorrow)
        self.assertEqual(self.send.call_count, 3)
        self.assertEqual(len(self.state.sent), 1)

    def test_healthy_symbol_still_processed_when_another_is_invalid(self):
        self.args.stock_symbols = ["BAD", "AAPL"]
        self.assertTrue(self.monitor.check(NOW))
        self.assertEqual(self.send.call_count, 1)

    def test_forbidden_topic_is_a_visible_failure(self):
        self.send.side_effect = HTTPError(
            "https://ntfy.sh/", 403, "Forbidden", {}, None
        )
        with self.assertRaisesRegex(main.MonitorError, "HTTP 403"):
            self.monitor.check(NOW)
        self.assertEqual(self.state.sent, set())

    def test_corrupt_state_fails_visibly(self):
        for contents in ["not JSON", "[]", '{"day":"2026-11-09","sent":[12]}']:
            self.path.write_text(contents)
            with self.assertRaises(main.MonitorError):
                main.AlertState(self.path)


class NtfyTests(unittest.TestCase):
    @patch("main.urlopen")
    def test_publish_sends_urgent_notification_and_checks_acceptance(self, open_url):
        topic = "unit-test-topic"
        receipt = {"id": "abc123", "event": "message", "topic": topic}
        open_url.return_value = io.BytesIO(json.dumps(receipt).encode())
        main.publish_ntfy(topic, "SLS ALERT UP", "Price: 30.23")
        request = open_url.call_args.args[0]
        self.assertEqual(open_url.call_args.kwargs["timeout"], 15)
        self.assertEqual(request.full_url, "https://ntfy.sh/")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data),
            {
                "topic": topic,
                "title": "SLS ALERT UP",
                "message": "Price: 30.23",
                "priority": 5,
            },
        )

    @patch("main.urlopen")
    def test_invalid_publish_receipt_is_a_failure(self, open_url):
        for body in [
            b"not-json",
            b"{}",
            b'{"id":"abc","event":"message","topic":"other"}',
        ]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                open_url.return_value = io.BytesIO(body)
                main.publish_ntfy("unit-test-topic", "SLS ALERT UP", "Price: 30.23")


class RuntimeTests(unittest.TestCase):
    @patch("main.time.sleep")
    @patch("main.datetime")
    @patch("main.Ticker")
    def test_repeated_failures_terminate_and_close_session(self, ticker, date, sleep):
        date.now.return_value = NOW
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            args = main.parse_args(
                ["--state_file", str(Path(directory) / "state.json")]
            )
            ticker.return_value.quotes = {"AAPL": "bad quote"}
            with self.assertRaisesRegex(main.MonitorError, "Five consecutive"):
                main.run(args)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [20, 40, 60, 60])
        ticker.return_value.session.close.assert_called_once()

    @patch("main.time.sleep")
    @patch("main.datetime")
    @patch("main.Ticker")
    def test_runtime_limit_is_normal_success(self, ticker, date, sleep):
        date.now.return_value = NOW
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            args = main.parse_args(
                [
                    "--state_file",
                    str(Path(directory) / "state.json"),
                    "--max_runtime_seconds",
                    "1",
                ]
            )
            with (
                patch("main.Monitor.check", return_value=False),
                patch("main.time.monotonic", side_effect=[0, 0, 0, 1]),
            ):
                main.run(args)
        sleep.assert_called_once_with(1)

    @patch("main.datetime")
    @patch("main.Ticker")
    def test_close_and_weekends_exit_without_network(self, ticker, date):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV, clear=True),
        ):
            args = main.parse_args(
                [
                    "--state_file",
                    str(Path(directory) / "state.json"),
                    "--exit_at_market_close",
                ]
            )
            for now in [NOW.replace(hour=20), NOW - timedelta(days=2)]:
                date.now.return_value = now
                main.run(args)
        ticker.assert_not_called()

    def test_main_returns_nonzero_on_operational_failure(self):
        with (
            patch("main.parse_args"),
            patch("main.run", side_effect=main.MonitorError("test")),
        ):
            self.assertEqual(main.main([]), 1)


if __name__ == "__main__":
    unittest.main()
