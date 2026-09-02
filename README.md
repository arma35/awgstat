# AWGStat

Traffic statistics for AmneziaWG running in Docker.

Collects per-peer RX/TX deltas from `wg show … dump` inside the container and
publishes a static, SARG-style HTML report tree.

Generated reports include:

- a dashboard with today, current week, current month, and retained totals;
- daily, weekly, and monthly report archives;
- a traffic-ranked user table for every period;
- a user page with daily, hourly, and raw interval breakdowns;
- RX, TX, total traffic, percentage share, IP, and activity timestamps.

AmneziaWG does not expose HTTP destinations, so AWGStat cannot produce SARG's
sites, URLs, denied requests, or download reports.

## Version

See [`VERSION`](VERSION). Current: **1.2.1**

## Requirements

- Docker container with AmneziaWG (`CONTAINER` in `config`)
- Python 3
- Web root writable by the cron user (default: root)
- `flock`, `awk`, `tar`

## Install / upgrade

```bash
curl -fsSL -O https://github.com/arma35/awgstat/releases/download/v1.2.1/awgstat-1.2.1.tar.gz
tar -xzf awgstat-1.2.1.tar.gz
cd awgstat-1.2.1
sudo bash install.sh
```

Cron lives in **`/etc/cron.d/awgstat`** (system), not in `crontab -l`. Check with `cat /etc/cron.d/awgstat`.

Upgrade **does not** overwrite: `names.map`, `history.csv`, `last.db`, `backups/`, or your local `config` values (only bumps `VERSION` and adds new keys if missing).

Default install path: `/opt/wgstats`  
Cron: collect every minute, HTML every 5 minutes, forced HTML rebuild at 00:01 UTC+3, backup check daily at 03:00.

## Config

| Key | Meaning |
|-----|---------|
| `VERSION` | Package version |
| `CONTAINER` | Docker container name |
| `WG_INTERFACE` | Interface inside container |
| `WEBROOT` | HTML output directory |
| `TITLE` | Page title |
| `RETENTION_DAYS` | History retention |
| `REPORT_TZ` | Timezone for Today + midnight rebuild (IANA, default `Europe/Moscow`) |
| `DAILY_REPORTS` | Number of daily archive periods (`0` = all retained history) |
| `WEEKLY_REPORTS` | Number of weekly archive periods (`0` = all retained history) |
| `MONTHLY_REPORTS` | Number of monthly archive periods (`0` = all retained history) |
| `DETAIL_ROWS` | Maximum raw intervals on a user page (`0` = hide) |
| `BACKUP_DAYS` | Minimum days between data backups |
| `BACKUP_DIR` | Where `.tar.gz` backups are stored |
| `LAST_BACKUP` | Timestamp file of last successful backup |

Map peer public keys to names in `names.map` (**colon** separator — keys often end with `=`):

```
<base64-public-key>:phone
```

Unknown peers that generate traffic are appended automatically as `неизвестный`.

## Backup

```bash
sudo /opt/wgstats/backup.sh          # only if BACKUP_DAYS elapsed
sudo /opt/wgstats/backup.sh --force  # always
```

Archive contents: `names.map`, `history.csv`, `last.db`, `config`, `VERSION`.

## Generated report tree

```text
index.html
reports/
├── daily/
│   ├── index.html
│   └── YYYY-MM-DD/
│       ├── index.html
│       └── users/<peer-id>.html
├── weekly/
│   ├── index.html
│   └── YYYY-Www/...
└── monthly/
    ├── index.html
    └── YYYY-MM/...
```

The generator builds the complete tree in a temporary directory and publishes
it only after all pages are ready. Files outside `index.html`, `style.css`, and
`reports/` in `WEBROOT` are left untouched.

## Layout

```
wgstats.sh           # collector
htmlgen.py           # HTML generator entry point
reportgen.py         # SARG-style report implementation
backup.sh            # data backup
config               # settings
style.css            # report CSS
names.map            # peer → name (local, not in release)
VERSION
cron/awgstat
install.sh
```
