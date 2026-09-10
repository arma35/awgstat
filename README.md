# AWGStat

Traffic statistics for AmneziaWG running in Docker.

Collects per-peer RX/TX deltas from `wg show … dump` inside the container and
publishes a static report tree that follows the classic SARG 2.4 layout and
navigation model.

Generated reports include:

- a SARG-style report index with daily, weekly, monthly, all-time, and online
  reports;
- a minute-updated online report with collector freshness, current peer state,
  RX/TX rates, today/month/rolling-30-day totals, recent rankings, and
  total/per-user time-series graphs;
- interactive total and per-user traffic graphs with 60-minute, 6-hour,
  12-hour, 24-hour, 7-day, 30-day, previous-calendar-month, and custom
  date/time ranges;
- classic `DDMonYYYY-DDMonYYYY` report directories;
- a `Top users` table with `NUM`, date/time and graph links, `USERID`,
  `USERIP`, `CONNECT`, RX/TX, bytes, percentage, total, and average rows;
- a SARG-style user report for every peer;
- a 24-hour date/time matrix and an RX/TX graph for every user and period;
- SARG-compatible `sarg-date`, `sarg-users`, and `sarg-general` metadata.

AmneziaWG does not expose HTTP destinations, so AWGStat cannot produce SARG's
sites, URLs, denied requests, or download reports. `CONNECT` therefore means
AWGStat traffic sampling intervals, not TCP connections.

## Version

See [`VERSION`](VERSION). Current: **2.3.3**

## Requirements

- Docker container with AmneziaWG (`CONTAINER` in `config`)
- Python 3
- Web root writable by the cron user (default: root)
- `flock`, `awk`, `tar`

## Install / upgrade

```bash
curl -fsSL -O https://github.com/arma35/awgstat/releases/download/v2.3.3/awgstat-2.3.3.tar.gz
tar -xzf awgstat-2.3.3.tar.gz
cd awgstat-2.3.3
sudo bash install.sh
```

Starting with **2.3.2**, `install.sh` also installs the updater itself as
`/opt/wgstats/update.sh`. Older installations that do not have this file need
the one-time archive/bootstrap installation above. Later upgrades can use:

```bash
sudo /opt/wgstats/update.sh <version>
```

Cron lives in **`/etc/cron.d/awgstat`** (system), not in `crontab -l`. Check with `cat /etc/cron.d/awgstat`.

Upgrade **does not** overwrite: `names.map`, `history.csv`, `last.db`, `backups/`, or your local `config` values (only bumps `VERSION` and adds new keys if missing).

Default install path: `/opt/wgstats`  
Cron: serialized collection + online publication every minute, archive refresh
every 5 minutes, forced HTML rebuild at 00:01 UTC+3, backup check daily at
03:00.

## Releases

Every published change increments `VERSION`. A push to `main` runs the GitHub
Actions release workflow, validates shell/Python code, builds
`awgstat-<version>.tar.gz` plus SHA-256 checksum, creates tag `v<version>`, and
publishes an immutable GitHub Release. Reusing a version whose tag points to a
different commit is rejected.

## Config

| Key | Meaning |
|-----|---------|
| `VERSION` | Package version |
| `CONTAINER` | Docker container name |
| `WG_INTERFACE` | Interface inside container |
| `AUTO_DISCOVER_NAMES` | Import missing peer names from Amnezia `clientsTable` (`1` = enabled) |
| `AMNEZIA_CLIENTS_TABLE` | Path to Amnezia client metadata inside the container |
| `WEBROOT` | HTML output directory |
| `TITLE` | Page title |
| `RETENTION_DAYS` | History retention |
| `REPORT_TZ` | Timezone for Today + midnight rebuild (IANA, default `Europe/Moscow`) |
| `DAILY_REPORTS` | Number of daily archive periods (`0` = all retained history) |
| `WEEKLY_REPORTS` | Number of weekly archive periods (`0` = all retained history) |
| `MONTHLY_REPORTS` | Number of monthly archive periods (`0` = all retained history) |
| `DETAIL_ROWS` | Maximum raw intervals on a user page (`0` = hide) |
| `ONLINE_STATE` | Atomic current WireGuard snapshot written by the collector |
| `ONLINE_WINDOW_MINUTES` | Recent summary/ranking window and default graph preset |
| `ONLINE_ACTIVE_MINUTES` | Recent-handshake activity threshold (default `3`) |
| `ONLINE_STALE_MINUTES` | Collector heartbeat stale threshold (default `3`) |
| `ONLINE_REFRESH_SECONDS` | Browser auto-refresh interval (default `60`) |
| `BACKUP_DAYS` | Minimum days between data backups |
| `BACKUP_DIR` | Where `.tar.gz` backups are stored |
| `LAST_BACKUP` | Timestamp file of last successful backup |

Map peer public keys to names in `names.map` (**colon** separator — keys often end with `=`):

```
<base64-public-key>:phone
```

### Automatic names from Amnezia

With `AUTO_DISCOVER_NAMES=1` (default), the collector checks each peer seen in
the current WireGuard dump. If its public key is absent from `names.map`,
AWGStat reads `/opt/amnezia/awg/clientsTable` from the Amnezia container and
imports the matching `clientId -> userData.clientName` pair.

The Amnezia table is fetched lazily and at most once per collector cycle.
Existing `names.map` entries are never overwritten, so a manually edited name
acts as a permanent local override. If the client is not present in
`clientsTable`, AWGStat leaves it unnamed and retries on a later cycle instead
of writing a permanent `неизвестный` placeholder.

## Backup

```bash
sudo /opt/wgstats/backup.sh          # only if BACKUP_DAYS elapsed
sudo /opt/wgstats/backup.sh --force  # always
```

Archive contents: `names.map`, `history.csv`, `last.db`, `online.csv`, `config`,
`VERSION`.

## Online report semantics

`ONLINE REPORT` is rebuilt after the current collection cycle and auto-refreshes
in the browser. `TRAFFIC` means byte counters changed in the latest sample;
`ACTIVE` means a recent WireGuard handshake; `IDLE` means neither condition is
true. These states are measurements, not persistent VPN sessions. If the
collector heartbeat exceeds `ONLINE_STALE_MINUTES`, current rates are hidden
and the report is marked `STALE`.

The current-peers table shows per-user totals for `TODAY` and `THIS MONTH` in
`REPORT_TZ`, plus a rolling `LAST 30 DAYS` total. These values come from
AWGStat history. `WG COUNTERS` remains the kernel counter since the WireGuard
interface was created and can reset when that interface is recreated.

Both the total ONLINE graph and each ONLINE user graph have the same period
selector: last 60 minutes, 6 hours, 12 hours, 24 hours, 7 days, 30 days,
`PREVIOUS MONTH`, and `CUSTOM DATE / TIME`. `PREVIOUS MONTH` means the complete
previous calendar month in `REPORT_TZ` (for example, during September it is
August 1 through August 31). Long and custom ranges are rendered in the browser
from compact per-minute rate data; per-user pages receive only that user's
history data.

Graphs and recent rankings use actual AWGStat traffic intervals. Missing
minutes are rendered as zero. No site, URL, or application data is inferred.

## Generated report tree

```text
index.html
style.css
images/
├── awgstat.svg
├── datetime.svg
└── graph.svg
daily/
├── index.html
└── DDMonYYYY-DDMonYYYY/
    ├── index.html
    ├── sarg-date
    ├── sarg-users
    ├── sarg-general
    └── <peer-id>/
        ├── <peer-id>.html
        ├── d<peer-id>.html
        ├── graph.html
        └── graph.svg
weekly/
└── ...same SARG report layout...
monthly/
└── ...same SARG report layout...
total/
└── ...one report for all retained history...
online/
├── index.html
├── graph-controls.js
├── traffic-history.json
├── traffic-60-<timestamp>.svg
├── traffic-360-<timestamp>.svg
├── traffic-720-<timestamp>.svg
├── traffic-1440-<timestamp>.svg
└── <peer-id>/
    ├── index.html
    ├── traffic-history.json
    └── traffic-<timestamp>.svg
```

The generator builds the complete tree in a temporary directory and publishes
it only after all pages are ready. Files outside `index.html`, `style.css`,
`images/`, `daily/`, `weekly/`, `monthly/`, `total/`, and `online/` in
`WEBROOT` are left untouched.

## Layout

```
wgstats.sh           # collector
awgstat-cycle.sh     # serialized minute collection + report publication
htmlgen.py           # HTML generator entry point + ONLINE period enhancement
reportgen.py         # SARG-style report implementation
amnezia_names.py     # parse Amnezia clientsTable for automatic peer names
online.js            # interactive ONLINE graph period selector
backup.sh            # data backup
update.sh            # release updater (installed to /opt/wgstats since 2.3.2)
config               # settings
style.css            # report CSS
names.map            # peer → name cache/override (local, not in release)
VERSION
cron/awgstat
install.sh
```
