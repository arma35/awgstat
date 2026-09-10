# Changelog

## [2.4.0] — 2026-09-10

### Changed
- `names.map` is no longer used as the source of client names.
- Current names are read directly from Amnezia `/opt/amnezia/awg/clientsTable`
  using `clientId -> userData.clientName` on every collection/report cycle.
- The current Amnezia name overrides historical labels for the same public key;
  after revocation, reports fall back to the last non-empty name stored in
  `history.csv` without deleting traffic history.
- `NAMES` and `AUTO_DISCOVER_NAMES` were removed from the active configuration.
  Existing legacy `names.map` files are left untouched but ignored.
- Backups no longer include `names.map`; `history.csv` already preserves the
  last-known name for retired peers.

### Added
- A small internal fingerprint of current `clientId -> clientName` pairs so
  add/rename/revoke events trigger a full HTML rebuild even when no traffic was
  transferred at that moment.

## [2.3.4] — 2026-09-10

### Added
- A traffic-volume table under the total ONLINE graph and every ONLINE user graph.
- Exact RX/TX byte totals in `traffic-history.json` alongside the existing rate points.
- JavaScript syntax validation (`node --check online.js`) in the release workflow.

### Changed
- `LAST 60 MINUTES` is shown as 12 five-minute traffic rows.
- `LAST 6 HOURS`, `LAST 12 HOURS` and `LAST 24 HOURS` are shown hourly.
- `LAST 7 DAYS`, `LAST 30 DAYS` and `PREVIOUS MONTH` are shown daily.
- Custom periods up to and including 24 hours are shown hourly; longer custom
  periods are shown daily.
- Traffic tables include zero buckets and an exact RX/TX/TOTAL summary row.

## [2.3.3] — 2026-09-10

### Added
- `LAST 7 DAYS`, `LAST 30 DAYS` and `PREVIOUS MONTH` presets to the total
  ONLINE traffic graph.
- The same graph-period selector on every ONLINE user page, including
  60 minutes, 6/12/24 hours, 7/30 days, previous calendar month and custom
  date/time range.
- Compact per-user `traffic-history.json` files so long and custom ranges can
  be rendered locally without exposing other peers' traffic.

### Changed
- `PREVIOUS MONTH` is calculated as the complete previous calendar month in
  `REPORT_TZ`; during September, for example, it covers August 1 through 31.
- Long graph ranges are bucketed in the browser to keep SVG rendering compact.

## [2.3.2] — 2026-09-10

### Fixed
- `install.sh` now installs `update.sh` into `/opt/wgstats/update.sh`, so future
  release upgrades can be launched directly from the installed application.
- `update.sh` is included in CRLF normalization during installation.

### Changed
- README now documents the one-time bootstrap upgrade path for installations
  created before the updater was copied into `/opt/wgstats`.

## [2.3.1] — 2026-09-09

### Added
- GitHub Actions workflow `.github/workflows/release.yml` for automatic release
  publication from `main`.
- Automatic validation of `VERSION`, `config`, `README.md`, `CHANGELOG.md`,
  shell syntax, Python compilation and unit tests before publishing.
- Release archive `awgstat-<version>.tar.gz` and matching SHA-256 checksum.

### Changed
- Release tags and GitHub Releases are now created automatically after a
  successful push to `main`.
- Reusing an already published version for another commit is rejected, keeping
  releases immutable.

## [2.3.0] — 2026-09-09

### Added
- Автоматическое обнаружение имён клиентов AmneziaWG из
  `/opt/amnezia/awg/clientsTable`.
- Сопоставление `clientId` → `userData.clientName` для новых WireGuard-пиров,
  которых ещё нет в локальном `names.map`.
- Настройки `AUTO_DISCOVER_NAMES` и `AMNEZIA_CLIENTS_TABLE`.
- Отдельный parser `amnezia_names.py` и тесты формата `clientsTable`.

### Changed
- Новый пир получает имя при первом появлении в `wg show … dump`; таблица
  клиентов Amnezia читается лениво и не более одного раза за цикл коллектора.
- Существующие строки `names.map` никогда не перезаписываются автоматически и
  остаются ручными override-значениями.
- Если `clientName` не найден или `clientsTable` временно недоступен, AWGStat
  больше не фиксирует постоянное имя `неизвестный`: ключ остаётся без имени и
  будет проверен снова на следующем цикле.

## [2.2.0] — 2026-09-03

### Added
- Выпадающий выбор периода общего ONLINE-графика: последние 60 минут,
  6 часов, 12 часов и 24 часа.
- Пользовательский диапазон `FROM`/`TO` с датой и временем.
- Компактные обезличенные данные графика для локального построения
  произвольного диапазона без серверного API.

### Changed
- Четыре стандартных диапазона публикуются как готовые SVG; полная история
  загружается браузером только при выборе пользовательского диапазона.
- Выбранный диапазон сохраняется в URL и переживает минутное автообновление.

## [2.1.1] — 2026-09-03

### Added
- В таблицу текущих WireGuard-пиров перед `WG COUNTERS` добавлены столбцы
  `TODAY`, `THIS MONTH` и `LAST 30 DAYS`.
- Дневной и месячный трафик считается в часовом поясе отчёта, а последние
  30 дней — как скользящее 30-дневное окно по сохранённой истории AWGStat.

## [2.1.0] — 2026-09-03

### Added
- Пятый пункт `ONLINE REPORT` в индексе и навигации SARG.
- Текущий атомарный снимок всех WireGuard-пиров с heartbeat коллектора,
  RX/TX-дельтами, скоростями, handshake, счётчиками и состояниями
  `TRAFFIC`/`ACTIVE`/`IDLE`/`STALE`.
- Общий и пользовательские графики скорости, рейтинг трафика за настраиваемое
  недавнее окно и автоматически обновляемые пользовательские страницы.
- Настройки `ONLINE_WINDOW_MINUTES`, `ONLINE_ACTIVE_MINUTES`,
  `ONLINE_STALE_MINUTES` и `ONLINE_REFRESH_SECONDS`.

### Changed
- Сбор и online-публикация выполняются последовательно каждую минуту под одним
  lock; полное архивное дерево по-прежнему обновляется каждые пять минут.
- Минутное обновление атомарно заменяет только `index.html` и `online/`, не
  перестраивая все архивы без необходимости.
- Установка теперь завершается ошибкой, если обязательная генерация HTML не
  прошла, вместо сокрытия ошибки.

### Notes
- ONLINE показывает фактические интервалы AWGStat и свежесть handshake, а не
  выдуманные постоянные VPN-сессии, сайты или URL.

## [2.0.1] — 2026-09-03

### Added
- Четвёртый пункт `ALL TIME REPORT` под дневным, недельным и месячным
  отчётами, как сводка «Вся история» в AWGStat 1.2.1.
- Полный SARG-отчёт за всю сохранённую историю: рейтинг пользователей,
  страницы пользователей, матрицы по времени и RX/TX-графики.

## [2.0.0] — 2026-09-02

### Changed
- Интерфейс отчётов полностью перестроен по классическому SARG 2.4:
  Tahoma/Verdana 9 px, зелёные заголовки, `blanchedalmond`-шапки,
  `lavender`-ячейки, синие ссылки и компактные центрированные таблицы.
- Вместо современного dashboard используется индекс отчётов SARG с отдельными
  каталогами `daily`, `weekly`, `monthly`.
- Периоды получили SARG-имена `DDMonYYYY-DDMonYYYY`, а страница периода —
  структуру `Top users`, сортировку по байтам и строки `TOTAL`/`AVERAGE`.
- Публикация атомарно заменяет все каталоги AWGStat 2.0.0, сохраняет посторонние
  файлы webroot и удаляет старое дерево `reports/` только после успеха.

### Added
- Пользовательские каталоги и страницы в стиле SARG:
  `<user>/<user>.html`, `<user>/d<user>.html`, `<user>/graph.html`.
- 24-часовая матрица `DATE/TIME`, отдельный RX/TX-график и компактные иконки
  переходов из рейтинга пользователей.
- Метаданные `sarg-date`, `sarg-users`, `sarg-general` в каждом периоде.
- Тесты SARG-дерева, локальных ссылок и удаления устаревшего дерева при
  сохранении чужих web-файлов.

### Notes
- AmneziaWG не предоставляет сайты и URL. AWGStat не выдумывает эти данные:
  `CONNECT` обозначает интервалы измерения трафика, а не TCP-соединения.

## [1.2.1] — 2026-09-02

### Fixed
- Добавлены обязательные LF-окончания строк для shell-скриптов, Python-файлов
  и конфигурации, чтобы release-архивы корректно устанавливались на Linux.

### Added
- Правило проекта: любое публикуемое изменение выпускается новым неизменяемым
  релизом с отдельной версией, архивом, бэкапом и проверкой перед деплоем.

## [1.2.0] — 2026-09-02

### Added
- SARG-подобное дерево статических HTML-отчётов: день, неделя и месяц.
- Архив периодов с рейтингом пользователей по общему трафику.
- Детальная страница пользователя с RX/TX, долей трафика, разбивкой по дням,
  часам и последним интервалам.
- Настройки глубины архивов `DAILY_REPORTS`, `WEEKLY_REPORTS`,
  `MONTHLY_REPORTS` и лимит `DETAIL_ROWS`.
- Атомарная публикация дерева `reports/` после успешной генерации.

### Changed
- Главная страница стала сводным экраном с календарными периодами и архивами.
- Генератор разделён на точку входа `htmlgen.py` и модуль `reportgen.py`.

## [1.1.5] — 2026-08-05

### Fixed
- **Реальный формат `names.map` — `pubkey:name` (двоеточие).** Pubkey base64 часто оканчивается на `=`, поэтому сплит/`printf` через `=` ломал имена и плодил `==неизвестный`.
- `install.sh` при апдейте чинит `names.map` в канонический вид `key:name` и сразу пересобирает HTML.

### Added
- `REPORT_TZ` в config (по умолчанию `Europe/Moscow`) — часовой пояс для колонки `Today` и для cron `00:01`.

## [1.1.4] — 2026-08-05

### Fixed
- Парсинг `names.map` по **последнему** `=` (base64-ключи WireGuard часто оканчиваются на `=`). Старый сплит по первому `=` давал `==неизвестный` спам и игнорировал переименования.
- Автоочистка дублей в `names.map` при запуске коллектора (оставляет нормальное имя, выкидывает мусор `неизвестный`).

## [1.1.3] — 2026-08-05

### Fixed
- Принудительная перерисовка HTML перенесена на `00:01` в часовом поясе `UTC+3` (через `CRON_TZ=Europe/Moscow`), чтобы `Today` корректно обнулялся после полуночи по вашему времени.

## [1.1.1] — 2026-08-04

### Fixed
- names.map edits schedule HTML rebuild within ~1 minute (stamp + `.changed`), even with no new traffic
- `install.sh` prints full `/etc/cron.d/awgstat` (backup job is there; not shown by `crontab -l`)
- Clearer comments in cron file for the 03:00 backup line

## [1.1.2] — 2026-08-05

### Fixed
- Ежедневный принудительный пересчёт HTML в `00:05`, чтобы колонка `Today` не показывала трафик предыдущего дня, если сегодня трафика не было.

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
