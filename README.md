# Stock market alerts

Get an urgent ntfy push on your phone when a watched US stock crosses a percentage threshold in premarket, regular trading, or postmarket. The notification includes the ticker, direction, price, percentage change, session, and Eastern quote time.

A threshold of `.10` means 10% in either direction. The monitor uses Yahoo's change for the current session. Regular trading compares the price with the previous close. Premarket and postmarket use Yahoo's reference close for that session.

## Connect your phone

1. Install the [ntfy app](https://ntfy.sh/) on your phone and allow notifications.
2. Generate a topic in a private terminal with `python3 -c 'import secrets; print(secrets.token_hex(24))'`. Anyone who knows an unreserved topic can subscribe or publish to it. Keep the topic out of issues, pull requests, and logs.
3. In the app, subscribe to that exact topic on `https://ntfy.sh`. Multiple phones can subscribe to the same topic. Configure your phone's sound and Focus or Do Not Disturb settings if you want audible alerts.
4. In GitHub, open **Settings → Secrets and variables → Actions** and configure the repository secrets below. When migrating an existing installation, add `NTFY_TOPIC` before deploying this version.

| Repository secret | Value |
| --- | --- |
| `NTFY_TOPIC` | The topic name from step 2 |
| `STOCK_SYMBOLS` | Up to five symbols separated by spaces, e.g. `SLS CAPR OKYO` |
| `PERCENT_CHANGE_THRESHOLD` | Fractional threshold, e.g. `.15` for 15% |

The runner publishes directly to ntfy over HTTPS at priority 5. The log records when ntfy accepts the message. To check delivery on your device, use the local test below. [ntfy publishing guide](https://docs.ntfy.sh/publish/#publish-as-json)

## Run locally

Use Python 3.11 or newer. In Bash or Zsh, enter the same topic when `read` waits for input.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

read -rs NTFY_TOPIC
export NTFY_TOPIC

# Send one test push to the subscribed phone.
python -c 'import os; from main import publish_ntfy; publish_ntfy(os.environ["NTFY_TOPIC"], "Stock alerts test", "Phone delivery check")'

python main.py --stock_symbols SLS CAPR OKYO --percent_change_threshold .15
```

Watchlist, threshold, and topic can all be supplied as uppercase environment variables. Command-line arguments override them. Yahoo's public quote endpoint works without an API key.

## Monitoring and runner handoffs

- The monitor polls every 10 seconds, Monday–Friday, from 4 a.m. until 8 p.m. Eastern. Regular trading starts at 9:30 a.m. and postmarket starts at 4 p.m. Quotes must match the current Eastern date and session. The monitor continues polling on weekday exchange holidays and early closes.
- Each poll requests the whole watchlist in one Yahoo batch. A dropped connection or expired session gets one immediate retry with a fresh session. Throttling and server errors back off between polls. For HTTP 429 and 503, the monitor honors Yahoo's `Retry-After` when supplied.
- Each topic gets at most one accepted UP and one accepted DOWN alert per ticker per Eastern day while its saved state is available. The monitor writes hashed identifiers to `alert-state.json` after ntfy acknowledges publication. A failed push stays queued in memory for retry if the stock retraces. A process crash can lose an unaccepted push or repeat one whose receipt was lost.
- Five consecutive checks with unresolved quote or push errors fail the job. A bounded run also fails if it ends with an unresolved error. A permanent ntfy rejection such as HTTP 403 fails immediately. Check failed runs in Actions.
- The [cron workflow](.github/workflows/cron.yml) schedules an early start at 2:50 a.m. Eastern and recovery starts hourly through 7:50 p.m. A runner that starts before 4 a.m. waits for premarket. After up to 345 minutes of operation, a successful run saves its state and explicitly dispatches its successor if monitoring hours continue. A failure during monitoring hours gets one fresh runner. If that runner also fails, the next scheduled start handles recovery. Only the handoff job gets Actions write permission.
- GitHub serializes scheduled and dispatched runs. The next monitor restores the latest saved state, including state uploaded after a failure. The artifact is kept for one day. Runner provisioning still leaves a handoff gap, and GitHub may delay or drop the first scheduled start. Manual dispatch from the Actions tab also queues a monitor on the default branch. [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

Use `--poll_interval 30` to change polling, `--max_runtime_seconds 300` to bound a run, or `--state_file /path/to/state.json` for a persistent volume. With `--exit_at_market_close`, the monitor exits at 8 p.m. Eastern or on weekends. Otherwise a local process waits through quiet hours.

## Development

```bash
python -m unittest discover -s tests -v
```

Tests mock Yahoo and ntfy. The [test workflow](.github/workflows/test.yml) checks pull requests on Python 3.11 and 3.12 and validates the Actions workflows. The AWS Terraform/ECS files are a legacy deployment sketch and require additional configuration before use.
