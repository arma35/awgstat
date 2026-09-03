# Changelog

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
