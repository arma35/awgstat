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
- a traffic-volume table under every ONLINE graph, using 5-minute rows for the
  60-minute view, hourly rows for 6/12/24-hour views and custom ranges up to
  24 hours, and daily rows for 7/30-day, previous-month and longer custom
  ranges;
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

See [`VERSION`](VERSION). Current: **2.4.0**

## Requirements

- Docker container with AmneziaWG (`CONTAINER` in `config`)
- Python 3
- Web root writable by the cron user (default: root)
- `flock`, `awk`, `tar`

## Install / upgrade

```bash
curl -fsSL -O https://github.com/arma35/awgstat/releases/download/v2.4.0/awgstat-2.4.0.tar.gz
tar -xzf awgstat-2.4.0.tar.gz
cd awgstat-2.4.0
sudo bash install.sh
```

Starting with **2.3.2**, `install.sh` also installs the updater itself as
`/opt/wgstats/update.sh`. Older installations that do not have this file need
the one-time archive/bootstrap installation above. Later upgrades can use:

```bash
sudo /opt/wgstats/update.sh <version>
```

Cron lives in **`/etc/cron.d/awgstat`** (system), not in `crontab -l`. Check with `cat /etc/cron.d/awgstat`.

Upgrade **does not** overwrite: `history.csv`, `last.db`, `backups/`, or your
local config values. AWGStat 2.4 removes the obsolete `NAMES` and
`AUTO_DISCOVER_NAMES` config keys. If an old `/opt/wgstats/names.map` remains
on disk, it is left untouched but ignored.

Default install path: `/opt/wgstats`  
Cron: serialized collection + online publication every minute, archive refresh
every 5 minutes, forced HTML rebuild at 00:01 UTC+3, backup check daily at
03:00.

## Releases

Every published change increments `VERSION`. A push to `main` runs the GitHub
Actions release workflow, validates shell/Python/JavaScript code, builds
`awgstat-<version>.tar.gz` plus SHA-256 checksum, creates tag `v<version>`, and
publishes an immutable GitHub Release. Reusing a version whose tag points to a
different commit is rejected.

## Config

| Key | Meaning |
|-----|---------|
| `VERSION` | Package version |
| `CONTAINER` | Docker container name |
| `WG_INTERFACE` | Interface inside container |
| `AMNEZIA_CLIENTS_TABLE` | Authoritative Amnezia client metadata inside the container |
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

## Client names

AWGStat 2.4 does not maintain `names.map`.

The current display name is read directly from Amnezia's
`/opt/amnezia/awg/clientsTable`:

```text
clientId            -> WireGuard public key
userData.clientName -> display name
```

Name resolution is intentionally simple:

1. if the public key exists in the current Amnezia `clientsTable`, the current
   `clientName` is displayed everywhere, including historical reports;
2. if the peer has been revoked and no longer exists in `clientsTable`, AWGStat
   displays the last non-empty name already stored with that peer in
   `history.csv`;
3. if neither source has a name, the report falls back to an unknown/peer
   identifier.

This means renaming an existing Amnezia client changes its displayed name in
AWGStat without rewriting traffic history. Revoking a client does not delete
its historical traffic. A newly created client with the same human-readable
name is still a different user because traffic identity is the WireGuard public
key, not `clientName`.

The collector stores the current Amnezia name together with each traffic delta
and writes it into the current online snapshot. A small internal fingerprint of
current `clientId -> clientName` pairs is used only to trigger a report rebuild
when a client is added, renamed or revoked; it is not a user-managed name map.

Removing a client from AWGStat files does **not** revoke VPN access. VPN clients
must be revoked in Amnezia server management. Historical traffic remains until
it expires according to `RETENTION_DAYS`.

## Backup

```bash
sudo /opt/wgstats/backup.sh          # only if BACKUP_DAYS elapsed
sudo /opt/wgstats/backup.sh --force  # always
```

Archive contents: `history.csv`, `last.db`, `online.csv`, `config`, `VERSION`.
Client names do not need a separate backup: current names live in Amnezia and
last-known names are already present in `history.csv`.

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
from compact per-minute data; per-user pages receive only that user's history.

Each ONLINE graph has a traffic-volume table directly below it. The table uses:

- `LAST 60 MINUTES`: 12 rows of 5 minutes;
- `LAST 6/12/24 HOURS`: one row per hour;
- `LAST 7 DAYS`, `LAST 30 DAYS`, `PREVIOUS MONTH`: one row per day;
- custom ranges up to and including 24 hours: one row per hour;
- custom ranges longer than 24 hours: one row per day.

The JSON feeding the browser keeps RX/TX rate values and also contains exact
RX/TX byte totals for each non-zero minute, so table totals do not have to be
estimated from graph rates. Empty buckets are shown as zero and a `TOTAL` row
summarizes the selected range.

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

```text
wgstats.sh           # collector; reads live names from Amnezia clientsTable
awgstat-cycle.sh     # serialized minute collection + report publication
htmlgen.py           # HTML generator entry point + current-name resolution
reportgen.py         # SARG-style report implementation
amnezia_names.py     # parse Amnezia clientsTable
online.js            # interactive ONLINE graph + traffic-table controls
backup.sh            # data backup
update.sh            # release updater (installed to /opt/wgstats since 2.3.2)
config               # settings
style.css            # report CSS
VERSION
cron/awgstat
install.sh
```
