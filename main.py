"""Monitor US stock moves and send push notifications through ntfy."""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import UTC, datetime
from datetime import time as clock_time
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from yahooquery import Ticker

LOG = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
PHASES = {
    "pre": (clock_time(4), clock_time(9, 30), "preMarket"),
    "regular": (clock_time(9, 30), clock_time(16), "regularMarket"),
    "post": (clock_time(16), clock_time(20), "postMarket"),
}


class MonitorError(RuntimeError):
    """An operational failure that should fail the job visibly."""


class QuoteError(ValueError):
    """A malformed quote or an error response from Yahoo."""


def retry_after_seconds(value):
    if not value:
        return 0
    try:
        if value.isdigit():
            return int(value)
        return max(0, parsedate_to_datetime(value).timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0


class YahooQuotes:
    def __init__(self, symbols):
        self.symbols = symbols
        self.ticker = None
        self.retry_after = 0

    def close(self):
        if self.ticker is not None:
            self.ticker.session.close()
            self.ticker = None

    def fetch(self):
        self.retry_after = 0
        for attempt in range(2):
            try:
                if self.ticker is None:
                    self.ticker = Ticker(
                        self.symbols, timeout=15, raise_for_status=True
                    )
                # v7 sends the whole watchlist in one request.
                quotes = self.ticker.quotes
                if not isinstance(quotes, dict) or not any(
                    s in quotes for s in self.symbols
                ):
                    raise QuoteError("Missing watchlist in Yahoo batch response")
                return quotes
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", 0)
                detail = type(exc).__name__
                if status:
                    detail += f" HTTP {status}"
                if code := getattr(exc, "code", 0):
                    detail += f" curl {code}"
                if status in (429, 503):
                    self.retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                # Reopen once for a dropped connection, expired crumb or malformed batch.
                if not status or status < 400 or status in (401, 403):
                    self.close()
                    if attempt == 0:
                        LOG.warning(
                            "Yahoo batch request failed (%s); reconnecting", detail
                        )
                        continue
                # Exception text and request URLs can include the session crumb.
                raise QuoteError(f"Yahoo batch request failed ({detail})") from None


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)

    def option(name, **kwargs):
        default = os.environ.get(name.upper())
        required = kwargs.pop("required", False)
        if kwargs.get("nargs") == "+" and default is not None:
            default = default.split()
        parser.add_argument(
            f"--{name}",
            default=default,
            required=not default and required,
            **kwargs,
        )

    option("stock_symbols", nargs="+", required=True)
    option(
        "percent_change_threshold",
        type=positive_float,
        required=True,
        help="Fractional threshold: .10 means 10%% in either direction",
    )
    option(
        "ntfy_topic",
        required=True,
        help="Topic subscribed to in the ntfy app on ntfy.sh",
    )
    option(
        "yahoo_api_key",
        help="Deprecated compatibility option; yahooquery does not use this key",
    )
    parser.add_argument("--state_file", type=Path, default=Path("alert-state.json"))
    parser.add_argument("--poll_interval", type=positive_float, default=10)
    parser.add_argument("--max_runtime_seconds", type=positive_float)
    parser.add_argument("--exit_at_market_close", action="store_true")
    args = parser.parse_args(argv)

    args.stock_symbols = list(
        dict.fromkeys(s.strip().upper() for s in args.stock_symbols)
    )
    if not args.stock_symbols or len(args.stock_symbols) > 5:
        parser.error(
            "provide one to five unique symbols; split larger watchlists explicitly"
        )
    if any(not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]*", s) for s in args.stock_symbols):
        parser.error("stock_symbols contains an invalid symbol")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.ntfy_topic):
        parser.error(
            "ntfy_topic must contain 1–64 letters, digits, underscores or hyphens"
        )
    return args


def market_phase(now):
    now = now.astimezone(EASTERN)
    if now.weekday() >= 5:
        return None
    for phase, (start, end, _) in PHASES.items():
        if start <= now.time() < end:
            return phase
    return None


class AlertState:
    """Daily ntfy acceptances, written atomically without the topic name."""

    def __init__(self, path):
        self.path = path
        self.day = None
        self.sent = set()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("day"), str)
                    or not isinstance(data.get("sent"), list)
                    or any(
                        not isinstance(key, str)
                        or not re.fullmatch(r"[a-f0-9]{64}", key)
                        for key in data["sent"]
                    )
                ):
                    raise ValueError("invalid state schema")
                self.day, self.sent = data["day"], set(data["sent"])
            except (ValueError, OSError) as exc:
                raise MonitorError(
                    "Cannot read alert state; restore or remove the state file"
                ) from exc

    def set_day(self, now):
        day = now.astimezone(EASTERN).date().isoformat()
        if day != self.day:
            self.day, self.sent = day, set()
            return True
        return False

    @staticmethod
    def key(symbol, direction, topic):
        return hashlib.sha256(f"{symbol}|{direction}|ntfy:{topic}".encode()).hexdigest()

    def record(self, key):
        self.sent.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"day": self.day, "sent": sorted(self.sent)}) + "\n"
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise MonitorError("Cannot save alert state") from exc


def raw(value):
    return value.get("raw") if isinstance(value, dict) else value


def quote_alert(quote, phase, threshold, now):
    if (
        not isinstance(quote, dict)
        or "error" in quote
        or not any(prefix + "Price" in quote for _, _, prefix in PHASES.values())
    ):
        raise QuoteError("Yahoo returned an error instead of a quote")
    start, end, prefix = PHASES[phase]
    values = [
        raw(quote.get(prefix + suffix)) for suffix in ("ChangePercent", "Price", "Time")
    ]
    if any(value is None for value in values):
        # Many symbols have no extended-hours trades. Missing regular data is an error.
        if phase != "regular":
            return None
        raise QuoteError("Missing regular-market quote fields")
    percent, price, timestamp = values
    if (
        any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            for v in values
        )
        or price <= 0
    ):
        raise QuoteError("Invalid numeric quote fields")
    try:
        quoted_at = datetime.fromtimestamp(timestamp, UTC).astimezone(EASTERN)
    except (ValueError, OSError, OverflowError) as exc:
        raise QuoteError("Invalid quote timestamp") from exc
    now = now.astimezone(EASTERN)
    if (
        quoted_at.date() != now.date()
        or not start <= quoted_at.time() < end
        or timestamp > now.timestamp() + 300
    ):
        # Reject yesterday's pre/post values and closed-market holiday quotes.
        return None
    # The v7 batch endpoint reports percentage points, e.g. 15.0 means +15%.
    change = percent / 100
    if abs(change) < threshold:
        return None
    direction = "UP" if change > 0 else "DOWN"
    return (
        direction,
        f"Price: {price:g}, Change: {change:+.2%} ({phase}, {quoted_at:%H:%M:%S %Z})",
    )


def publish_ntfy(topic, title, body):
    request = Request(
        "https://ntfy.sh/",
        data=json.dumps(
            {"topic": topic, "title": title, "message": body, "priority": 5}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        receipt = json.load(response)
    if (
        not isinstance(receipt, dict)
        or receipt.get("event") != "message"
        or receipt.get("topic") != topic
        or not isinstance(receipt.get("id"), str)
        or not receipt["id"]
    ):
        raise ValueError("Invalid publish receipt from ntfy")


class Monitor:
    def __init__(self, args, state, source):
        self.args, self.state, self.source = args, state, source
        self.pending = {}

    def queue_alerts(self, quotes, phase, now):
        errors = False
        for symbol in self.args.stock_symbols:
            try:
                alert = quote_alert(
                    quotes.get(symbol), phase, self.args.percent_change_threshold, now
                )
            except QuoteError as exc:
                LOG.error("%s: %s", symbol, exc)
                errors = True
                continue
            if alert is None:
                continue
            direction, body = alert
            key = self.state.key(symbol, direction, self.args.ntfy_topic)
            if key not in self.state.sent:
                self.pending.setdefault(key, (f"{symbol} ALERT {direction}", body))
        return errors

    def check(self, now):
        if self.state.set_day(now):
            self.pending.clear()
        phase = market_phase(now)
        if phase is None:
            return False
        try:
            errors = self.queue_alerts(self.source.fetch(), phase, now)
        except QuoteError as exc:
            LOG.error("%s", exc)
            errors = True

        # Retry the original alert even if the price retraces or the next quote request fails.
        for key, (title, body) in list(self.pending.items()):
            try:
                publish_ntfy(self.args.ntfy_topic, title, body)
            except HTTPError as exc:
                exc.close()
                if 400 <= exc.code < 500 and exc.code not in (408, 425, 429):
                    raise MonitorError(
                        f"ntfy rejected the publish (HTTP {exc.code}); check NTFY_TOPIC"
                    ) from None
                LOG.error("ntfy publish failed (HTTP %d); will retry", exc.code)
                errors = True
                continue
            except (OSError, HTTPException, ValueError) as exc:
                LOG.error("ntfy publish failed (%s); will retry", type(exc).__name__)
                errors = True
                continue
            self.state.record(key)
            del self.pending[key]
            LOG.info("ntfy accepted %s (%s)", title, body)
        return errors


def run(args):
    state = AlertState(args.state_file)
    LOG.info(
        "Monitoring %d symbols at a %.2f%% threshold; polling every %g seconds",
        len(args.stock_symbols),
        args.percent_change_threshold * 100,
        args.poll_interval,
    )
    if args.yahoo_api_key:
        LOG.warning(
            "yahooquery does not use yahoo_api_key; the compatibility option is ignored"
        )
    deadline = (
        time.monotonic() + args.max_runtime_seconds
        if args.max_runtime_seconds
        else math.inf
    )
    source = YahooQuotes(args.stock_symbols)
    monitor = Monitor(args, state, source)
    failures = 0
    try:
        while time.monotonic() < deadline:
            now = datetime.now(EASTERN)
            if args.exit_at_market_close and (
                now.weekday() >= 5 or now.time() >= clock_time(20)
            ):
                break
            if market_phase(now) is None:
                delay = 60
            else:
                if monitor.check(now):
                    failures += 1
                    if failures >= 5:
                        raise MonitorError(
                            "Five consecutive checks failed; monitoring is unhealthy"
                        )
                else:
                    failures = 0
                delay = max(
                    min(args.poll_interval * 2**failures, 60), source.retry_after
                )
                if args.exit_at_market_close:
                    close = now.replace(hour=20, minute=0, second=0, microsecond=0)
                    delay = min(delay, max(0, close.timestamp() - time.time()))
            time.sleep(max(0, min(delay, deadline - time.monotonic())))
    finally:
        source.close()
    if failures:
        raise MonitorError(
            "Monitoring ended with unresolved quote or delivery failures"
        )


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        run(args)
    except KeyboardInterrupt:
        LOG.info("Monitor stopped by user")
        return 130
    except (MonitorError, OSError, ValueError) as exc:
        LOG.error(
            "Monitor stopped: %s",
            str(exc) if isinstance(exc, MonitorError) else type(exc).__name__,
        )
        return 1
    LOG.info("Monitor completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
