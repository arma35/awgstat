# AWGStat

Traffic statistics for AmneziaWG running in Docker.

Collects per-peer RX/TX deltas from `wg show … dump` inside the container and publishes an HTML report.

## Version

See [`VERSION`](VERSION). Current: **1.1.2**

## Requirements

- Docker container with AmneziaWG (`CONTAINER` in `config`)
- Python 3
- Web root writable by the cron user (default: root)
- `flock`, `awk`, `tar`

## Install / upgrade

```bash
curl -fsSL -O https://github.com/arma35/awgstat/releases/download/v1.1.2/awgstat-1.1.2.tar.gz
tar -xzf awgstat-1.1.2.tar.gz
cd awgstat-1.1.2
sudo bash install.sh
```

Cron lives in **`/etc/cron.d/awgstat`** (system), not in `crontab -l`. Check with `cat /etc/cron.d/awgstat`.

Upgrade **does not** overwrite: `names.map`, `history.csv`, `last.db`, `backups/`, or your local `config` values (only bumps `VERSION` and adds new keys if missing).

Default install path: `/opt/wgstats`  
Cron: collect every minute, HTML every 5 minutes, backup check daily at 03:00.

## Config

| Key | Meaning |
|-----|---------|
| `VERSION` | Package version |
| `CONTAINER` | Docker container name |
| `WG_INTERFACE` | Interface inside container |
| `WEBROOT` | HTML output directory |
| `TITLE` | Page title |
| `RETENTION_DAYS` | History retention |
| `BACKUP_DAYS` | Minimum days between data backups |
| `BACKUP_DIR` | Where `.tar.gz` backups are stored |
| `LAST_BACKUP` | Timestamp file of last successful backup |

Map peer public keys to names in `names.map`:

```
<base64-public-key>=phone
```

Unknown peers that generate traffic are appended automatically as `неизвестный`.

## Backup

```bash
sudo /opt/wgstats/backup.sh          # only if BACKUP_DAYS elapsed
sudo /opt/wgstats/backup.sh --force  # always
```

Archive contents: `names.map`, `history.csv`, `last.db`, `config`, `VERSION`.

## Layout

```
wgstats.sh           # collector
htmlgen.py           # HTML generator
backup.sh            # data backup
config               # settings
style.css            # report CSS
names.map            # peer → name (local, not in release)
VERSION
cron/awgstat
install.sh
```
