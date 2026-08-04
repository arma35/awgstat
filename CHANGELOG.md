# Changelog

## [1.1.0] — 2026-08-04

### Added
- `backup.sh` — archives `names.map`, `history.csv`, `last.db`, `config`, `VERSION`
- `BACKUP_DAYS` / `BACKUP_DIR` / `LAST_BACKUP` in config (period in days)
- Cron daily at 03:00; backup runs only when the period has elapsed (`--force` to ignore)
- Auto-register unknown peers with traffic into `names.map` as `неизвестный` / `неизвестный-N`

### Changed
- HTML always uses current `names.map` (renames/swaps redraw on next htmlgen)
- htmlgen rebuilds when `names.map` is newer than `index.html`
- `install.sh` never overwrites `names.map`, `history.csv`, `last.db`, backups; keeps local `config` keys

## [1.0.0] — 2026-08-03

First versioned release of AWGStat (AmneziaWG traffic stats).

### Added
- Explicit `VERSION` file and `VERSION` in `config`
- Version shown in the HTML report header
- `install.sh` for deploy/upgrade to `/opt/wgstats`
- Packaged release tarball

### Changed
- Clean HTML report with shared `style.css`
- Collector writes deltas to `history.csv`; HTML rebuilds only when data changes
