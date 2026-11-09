import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import aiosmtplib

import main

NOW = datetime(2026, 11, 9, 10, tzinfo=main.EASTERN)
ENV = {
    "STOCK_SYMBOLS": "aapl AAPL msft",
    "PERCENT_CHANGE_THRESHOLD": ".10",
    "SENDER_EMAIL": "sender@example.com",
    "SENDER_EMAIL_HOST": "smtp.example.com",
    "SENDER_EMAIL_HOST_PORT": "587",
    "SENDER_EMAIL_PASSWORD": "test-password",
    "CARRIER": "verizon_pics",
    "PHONE_NUMBERS": "2025550100 2025550101",
}


def quote(change=0.10, when=NOW, phase="regular"):
    prefix = main.PHASES[phase][2]
    return {
        prefix + "ChangePercent": {"raw": change, "fmt": "10%"},
        prefix + "Price": {"raw": 11},
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
        self.assertEqual(
            args.recipients, ["2025550100@vzwpix.com", "2025550101@vzwpix.com"]
        )

    def test_cli_overrides_environment(self):
        self.assertEqual(
            self.parse(
                argv=["--percent_change_threshold", ".05"]
            ).percent_change_threshold,
            0.05,
        )

    def test_direct_email_without_carrier_or_phone(self):
        args = self.parse(
            {"CARRIER": "", "PHONE_NUMBERS": "", "RECIPIENT_EMAILS": "me@example.com"}
        )
        self.assertEqual(args.recipients, ["me@example.com"])

    def test_invalid_configuration_fails_before_any_network_access(self):
        cases = [
            {"PERCENT_CHANGE_THRESHOLD": value}
            for value in ["0", "-.1", "nan", "inf", "oops"]
        ]
        cases += [
            {"CARRIER": "invalid"},
            {"PHONE_NUMBERS": "123"},
            {"SENDER_EMAIL_HOST_PORT": "0"},
            {"STOCK_SYMBOLS": "A B C D E F"},
            {"RECIPIENT_EMAILS": "bad\naddress"},
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
                alert = main.quote_alert(quote(change, now, phase), phase, 0.10, now)
                self.assertEqual(alert[0], direction)
                self.assertIn(f"{change:+.2%}", alert[1])
                self.assertIsNone(
                    main.quote_alert(quote(change / 2, now, phase), phase, 0.10, now)
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


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "alert-state.json"
        with patch.dict(os.environ, ENV, clear=True):
            self.args = main.parse_args([])
        self.args.state_file = self.path
        self.args.stock_symbols = ["AAPL"]
        self.ticker = SimpleNamespace(price={"AAPL": quote()})
        self.state = main.AlertState(self.path)
        self.monitor = main.Monitor(self.args, self.state, self.ticker)
        self.send = AsyncMock()
        self.patcher = patch("main.send_email", self.send)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_success_suppressed_across_poll_and_process_restart(self):
        self.assertFalse(self.monitor.check(NOW))
        self.assertFalse(self.monitor.check(NOW))
        restarted = main.Monitor(self.args, main.AlertState(self.path), self.ticker)
        self.assertFalse(restarted.check(NOW))
        self.assertEqual(self.send.await_count, 2)
        contents = self.path.read_text()
        self.assertNotIn("202555", contents)
        self.assertNotIn("vzwpix", contents)
        self.assertEqual(len(json.loads(contents)["sent"]), 2)

    def test_failed_recipient_retried_without_resending_successful_recipient(self):
        self.send.side_effect = [None, OSError("temporary outage"), None]
        self.assertTrue(self.monitor.check(NOW))
        # Retry the original UP alert even after the price retraces below the threshold.
        self.ticker.price = {"AAPL": quote(0.01)}
        self.assertFalse(self.monitor.check(NOW))
        recipients = [call.args[1] for call in self.send.await_args_list]
        self.assertEqual(recipients, [*self.args.recipients, self.args.recipients[1]])
        self.assertEqual(len(self.state.sent), 2)

    def test_pending_send_retried_during_quote_outage(self):
        self.send.side_effect = [TimeoutError(), TimeoutError(), None, None]
        self.assertTrue(self.monitor.check(NOW))
        broken_ticker = Mock()
        type(broken_ticker).price = PropertyMock(side_effect=OSError())
        self.monitor.ticker = broken_ticker
        self.assertTrue(self.monitor.check(NOW))
        self.assertEqual(len(self.state.sent), 2)

    def test_reversal_and_next_day_are_new_alerts(self):
        self.monitor.check(NOW)
        self.ticker.price = {"AAPL": quote(-0.10)}
        self.monitor.check(NOW)
        tomorrow = NOW + timedelta(days=1)
        self.ticker.price = {"AAPL": quote(-0.10, tomorrow)}
        self.monitor.check(tomorrow)
        self.assertEqual(self.send.await_count, 6)
        self.assertEqual(len(self.state.sent), 2)

    def test_healthy_symbol_still_processed_when_another_is_invalid(self):
        self.args.stock_symbols = ["BAD", "AAPL"]
        self.assertTrue(self.monitor.check(NOW))
        self.assertEqual(self.send.await_count, 2)

    def test_authentication_failure_not_marked_sent(self):
        self.send.side_effect = aiosmtplib.SMTPAuthenticationError(
            535, "bad credentials"
        )
        with self.assertRaisesRegex(main.MonitorError, "authentication"):
            self.monitor.check(NOW)
        self.assertEqual(self.state.sent, set())

    def test_corrupt_state_fails_visibly(self):
        for contents in ["not JSON", "[]", '{"day":"2026-11-09","sent":[12]}']:
            self.path.write_text(contents)
            with self.assertRaises(main.MonitorError):
                main.AlertState(self.path)


class SMTPTests(unittest.TestCase):
    def test_success_is_not_dependent_on_server_response_wording_and_tls_port(self):
        with patch.dict(os.environ, ENV, clear=True):
            args = main.parse_args([])
        for port in [587, 465]:
            args.sender_email_host_port = port
            with patch(
                "main.aiosmtplib.send",
                new_callable=AsyncMock,
                return_value=({}, "Queued"),
            ) as send:
                asyncio.run(
                    main.send_email(args, "me@example.com", "AAPL ALERT UP", "Test")
                )
                self.assertEqual(send.call_args.kwargs["use_tls"], port == 465)
                self.assertEqual(send.call_args.kwargs["start_tls"], port != 465)
                self.assertEqual(send.call_args.args[0]["To"], "me@example.com")

    def test_refused_recipient_raises(self):
        with patch.dict(os.environ, ENV, clear=True):
            args = main.parse_args([])
        with (
            patch(
                "main.aiosmtplib.send",
                new_callable=AsyncMock,
                return_value=({"me@example.com": "refused"}, "Queued"),
            ),
            self.assertRaises(aiosmtplib.SMTPException),
        ):
            asyncio.run(main.send_email(args, "me@example.com", "Alert", "Test"))


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
            with (
                patch("main.Monitor.check", return_value=True) as check,
                self.assertRaisesRegex(main.MonitorError, "Five consecutive"),
            ):
                main.run(args)
        self.assertEqual(check.call_count, 5)
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
