# AWGStat

Traffic statistics for AmneziaWG running in Docker.

Collects per-peer RX/TX deltas from `wg show … dump` inside the container and publishes an HTML report.

## Version

See [`VERSION`](VERSION). Current: **1.0.0**

## Requirements

- Docker container with AmneziaWG (`CONTAINER` in `config`)
- Python 3
- Web root writable by the cron user (default: root)
- `flock`, `awk`

## Install / upgrade

```bash
sudo bash install.sh
# or from a release archive:
# tar -xzf awgstat-1.0.0.tar.gz && cd awgstat-1.0.0 && sudo bash install.sh
```

Default install path: `/opt/wgstats`  
Cron: `/etc/cron.d/awgstat` (collect every minute, HTML every 5 minutes)  
Report URL (this server): `/wgstats/`

## Config

Edit `/opt/wgstats/config` after install:

| Key | Meaning |
|-----|---------|
| `VERSION` | Package version (do not edit by hand) |
| `CONTAINER` | Docker container name |
| `WG_INTERFACE` | Interface inside container |
| `WEBROOT` | HTML output directory |
| `TITLE` | Page title |
| `RETENTION_DAYS` | History retention |

Map peer public keys to names in `names.map`:

```
<base64-public-key>=phone
```

## Layout

```
wgstats.sh    # collector
htmlgen.py    # HTML generator
config        # settings
style.css     # report CSS
names.map     # peer → name
VERSION       # semver
cron/awgstat  # cron snippet
install.sh    # installer
```
