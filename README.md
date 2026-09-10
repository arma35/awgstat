# AWGStat

Traffic statistics for AmneziaWG running in Docker.

Collects per-peer RX/TX deltas from `wg show … dump` inside the container and
publishes a static report tree that follows the classic SARG 2.4 layout and
navigation model.

Generated reports include:

- a SARG-style report index with daily, weekly, monthly, all-time, and online reports;
- a minute-updated online report with collector freshness, current peer state,
  RX/TX rates, today/month/rolling-30-day totals, recent rankings, and graphs;
- an interactive total-traffic graph with 60-minute, 6-hour, 12-hour,
  24-hour, and custom date/time ranges;
- classic `DDMonYYYY-DDMonYYYY` report directories;
- per-user report pages, date/time matrices and RX/TX graphs.

AmneziaWG does not expose HTTP destinations, so AWGStat cannot produce SARG's
sites, URLs, denied requests, or download reports. `CONNECT` means AWGStat
traffic sampling intervals, not TCP connections.

## Version

See [`VERSION`](VERSION). Current: **2.3.2**

## Requirements

- Docker container with AmneziaWG (`CONTAINER` in `config`)
- Python 3
- Web root writable by the cron user (default: root)
- `flock`, `awk`, `tar`, `curl`

## Install / upgrade

Fresh install or bootstrap upgrade from an older installation that does not yet
have `/opt/wgstats/update.sh`:

```bash
curl -fsSL -O https://github.com/arma35/awgstat/releases/download/v2.3.2/awgstat-2.3.2.tar.gz
tar -xzf awgstat-2.3.2.tar.gz
cd awgstat-2.3.2
sudo bash install.sh
```

Starting with 2.3.2, `install.sh` also installs the updater itself to
`/opt/wgstats/update.sh`, so later upgrades can be done with:

```bash
sudo /opt/wgstats/update.sh <version>
```

Cron lives in **`/etc/cron.d/awgstat`** (system), not in `crontab -l`. Check with:

```bash
cat /etc/cron.d/awgstat
```

Upgrade **does not** overwrite `names.map`, `history.csv`, `last.db`, `backups/`,
or local `config` values except `VERSION` and newly introduced missing keys.

Default install path: `/opt/wgstats`. Cron performs serialized collection and
online publication every minute, archive refresh every 5 minutes, forced HTML
rebuild at 00:01 in `REPORT_TZ`, and backup check daily at 03:00.

## Releases

Every published change increments `VERSION`. A push to `main` runs the GitHub
Actions release workflow, validates shell/Python code, builds
`awgstat-<version>.tar.gz` plus SHA-256 checksum, creates tag `v<version>`, and
publishes a GitHub Release. Reusing a version whose tag points to another commit
is rejected.

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
| `REPORT_TZ` | Timezone for Today + midnight rebuild |
| `DAILY_REPORTS` | Number of daily archive periods (`0` = all retained history) |
| `WEEKLY_REPORTS` | Number of weekly archive periods (`0` = all retained history) |
| `MONTHLY_REPORTS` | Number of monthly archive periods (`0` = all retained history) |
| `DETAIL_ROWS` | Maximum raw intervals on a user page (`0` = hide) |
| `ONLINE_STATE` | Atomic current WireGuard snapshot written by the collector |
| `ONLINE_WINDOW_MINUTES` | Recent summary/ranking window and default graph preset |
| `ONLINE_ACTIVE_MINUTES` | Recent-handshake activity threshold |
| `ONLINE_STALE_MINUTES` | Collector heartbeat stale threshold |
| `ONLINE_REFRESH_SECONDS` | Browser auto-refresh interval |
| `BACKUP_DAYS` | Minimum days between data backups |
| `BACKUP_DIR` | Where `.tar.gz` backups are stored |
| `LAST_BACKUP` | Timestamp file of last successful backup |

Map peer public keys to names in `names.map` using a **colon** separator:

```text
<base64-public-key>:phone
```

### Automatic names from Amnezia

With `AUTO_DISCOVER_NAMES=1` (default), the collector checks peers seen in the
current WireGuard dump. If a public key is absent from `names.map`, AWGStat
reads `/opt/amnezia/awg/clientsTable` from the Amnezia container and imports the
matching `clientId -> userData.clientName` pair.

The table is fetched lazily and at most once per collector cycle. Existing
`names.map` entries are never overwritten, so manually edited names act as local
overrides. If the client is absent from `clientsTable`, AWGStat leaves it unnamed
and retries on a later cycle instead of writing a permanent `неизвестный` value.

## Backup

```bash
sudo /opt/wgstats/backup.sh
sudo /opt/wgstats/backup.sh --force
```

Archive contents normally include `names.map`, `history.csv`, `last.db`,
`online.csv`, `config`, and `VERSION`.

## Online report semantics

`ONLINE REPORT` is rebuilt after the current collection cycle and auto-refreshes
in the browser. `TRAFFIC` means byte counters changed in the latest sample;
`ACTIVE` means a recent WireGuard handshake; `IDLE` means neither condition is
true. If the collector heartbeat exceeds `ONLINE_STALE_MINUTES`, current rates
are hidden and the report is marked `STALE`.

The current-peers table shows per-user totals for `TODAY` and `THIS MONTH` in
`REPORT_TZ`, plus a rolling `LAST 30 DAYS` total. `WG COUNTERS` is the kernel
counter since the WireGuard interface was created and can reset when that
interface is recreated.

## Layout

```text
wgstats.sh           # collector
awgstat-cycle.sh     # serialized minute collection + report publication
htmlgen.py           # HTML generator entry point
reportgen.py         # SARG-style report implementation
amnezia_names.py     # parse Amnezia clientsTable for automatic peer names
online.js            # interactive ONLINE graph period selector
backup.sh            # data backup
update.sh            # release downloader/updater; installed since 2.3.2
config               # settings
style.css            # report CSS
names.map            # peer → name cache/override (local, not in release)
VERSION
cron/awgstat
install.sh
```
