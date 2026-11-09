"""Monitor US stock moves and submit alerts through an SMTP server."""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import UTC, datetime
from datetime import time as clock_time
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosmtplib
from yahooquery import Ticker

LOG = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
CARRIER_MAP = {
    "verizon": "vtext.com",
    "verizon_pics": "vzwpix.com",
    "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "at&t": "txt.att.net",
    "boost": "smsmyboostmobile.com",
    "cricket": "sms.cricketwireless.net",
    "uscellular": "email.uscc.net",
}
PHASES = {
    "pre": (clock_time(4), clock_time(9, 30), "preMarket"),
    "regular": (clock_time(9, 30), clock_time(16), "regularMarket"),
    "post": (clock_time(16), clock_time(20), "postMarket"),
}


class MonitorError(RuntimeError):
    """An operational failure that should fail the job visibly."""


class QuoteError(ValueError):
    """A malformed quote or an error response from Yahoo."""


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
    option("sender_email", required=True)
    option("sender_email_host", required=True)
    option("sender_email_host_port", type=int, required=True)
    option("sender_email_password", required=True)
    option("carrier", choices=CARRIER_MAP)
    option("phone_numbers", nargs="+")
    option("recipient_emails", nargs="+", help="Optional direct email destinations")
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
    if len(args.stock_symbols) > 5:
        parser.error(
            "at most five unique symbols are supported; split larger watchlists explicitly"
        )
    if any(not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]*", s) for s in args.stock_symbols):
        parser.error("stock_symbols contains an invalid symbol")
    if not 1 <= args.sender_email_host_port <= 65535:
        parser.error("sender_email_host_port must be between 1 and 65535")
    if args.phone_numbers and not args.carrier:
        parser.error("carrier is required when phone_numbers are set")
    if args.carrier and args.carrier not in CARRIER_MAP:
        parser.error("carrier must be one of the supported carrier names")
    recipients = list(args.recipient_emails or [])
    for phone in args.phone_numbers or []:
        if not re.fullmatch(r"[0-9]{10}", phone):
            parser.error("phone_numbers must contain ten digits each")
        recipients.append(f"{phone}@{CARRIER_MAP[args.carrier]}")
    args.recipients = list(dict.fromkeys(recipients))
    if not args.recipients:
        parser.error("provide phone_numbers with carrier and/or recipient_emails")
    for address in [args.sender_email, *args.recipients]:
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", address):
            parser.error("email addresses must be valid single addresses")
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
    """Daily SMTP acceptances, written atomically without recipient addresses."""

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
    def key(symbol, direction, recipient):
        return hashlib.sha256(f"{symbol}|{direction}|{recipient}".encode()).hexdigest()

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
    # formatted=True preserves Yahoo's raw epoch timestamps and fractional changes.
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
    change, price, timestamp = values
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
    if abs(change) < threshold:
        return None
    direction = "UP" if change > 0 else "DOWN"
    return direction, f"Price: {price:g}, Change: {change:+.2%} ({phase})"


async def send_email(args, recipient, subject, body):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = (
        args.sender_email,
        recipient,
        subject,
    )
    message.set_content(body)
    refused, _ = await asyncio.wait_for(
        aiosmtplib.send(
            message,
            username=args.sender_email,
            password=args.sender_email_password,
            hostname=args.sender_email_host,
            port=args.sender_email_host_port,
            use_tls=args.sender_email_host_port == 465,
            start_tls=args.sender_email_host_port != 465,
            timeout=30,
        ),
        timeout=60,
    )
    # Success is determined by the library's exception/refusal contract, not "OK" text.
    if refused:
        raise aiosmtplib.SMTPException("SMTP refused a recipient")


class Monitor:
    def __init__(self, args, state, ticker):
        self.args, self.state, self.ticker = args, state, ticker
        self.pending = {}

    def check(self, now):
        if self.state.set_day(now):
            self.pending.clear()
        phase = market_phase(now)
        if phase is None:
            return False
        errors = False
        try:
            quotes = self.ticker.price
            if not isinstance(quotes, dict):
                raise QuoteError("Yahoo returned an invalid response")
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            LOG.error("Quote request failed (%s)", type(exc).__name__)
            quotes, errors = {}, True
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
            for recipient in self.args.recipients:
                key = self.state.key(symbol, direction, recipient)
                if key not in self.state.sent:
                    self.pending.setdefault(
                        key,
                        (recipient, f"{symbol} ALERT {direction}", f"{symbol} {body}"),
                    )

        # Retry the original alert even if the price retraces or the next quote request fails.
        for key, (recipient, subject, body) in list(self.pending.items()):
            try:
                asyncio.run(send_email(self.args, recipient, subject, body))
            except aiosmtplib.SMTPAuthenticationError as exc:
                raise MonitorError(
                    "SMTP authentication failed; check the sender credentials"
                ) from exc
            except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
                # SMTP error text may contain addresses; log only its type.
                LOG.error(
                    "Alert submission failed (%s); will retry", type(exc).__name__
                )
                errors = True
                continue
            self.state.record(key)
            del self.pending[key]
            LOG.info("SMTP accepted %s (phone delivery is not confirmed)", subject)
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
    ticker, monitor, failures = None, None, 0
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
                if ticker is None:
                    # Reuse cookies/connections; constructing a Ticker per symbol per poll is wasteful.
                    ticker = Ticker(args.stock_symbols, formatted=True, timeout=15)
                    monitor = Monitor(args, state, ticker)
                if monitor.check(now):
                    failures += 1
                    if failures >= 5:
                        raise MonitorError(
                            "Five consecutive checks failed; monitoring is unhealthy"
                        )
                else:
                    failures = 0
                delay = min(args.poll_interval * 2**failures, 60)
            time.sleep(max(0, min(delay, deadline - time.monotonic())))
    finally:
        if ticker is not None:
            ticker.session.close()
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
