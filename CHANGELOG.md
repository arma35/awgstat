# Changelog

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
