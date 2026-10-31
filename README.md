# Бот канала «Доступ к интернету»

Редакционная система для Telegram-канала про блокировки, обход DPI, VPN, сети и ИИ.
Собирает RSS, отсеивает мусор и дубли, оценивает материал, пишет публикацию в одном
из пяти форматов, собирает статистику и умеет работать в режиме подтверждения.

Работает на aiogram 3 + Groq + SQLite. Запуск по расписанию (GitHub Actions) одним
прогоном; команды админа — отдельным процессом.

- `telegrambot.py` — прогон: собрать → отфильтровать → оценить → опубликовать (или в очередь)
- `adminbot.py` — команды управления (очередь, статистика, ручная публикация, режим)
- `tools/collect_stats.py` — просмотры постов (окна 1ч / 6ч / 24ч)
- `tools/weekly_report.py` — недельный отчёт по эффективности
- `tools/dryrun.py` — сухой прогон без Groq и Telegram
- `tools/generate_examples.py` — примеры публикаций без отправки в канал

## Установка

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # заполнить GROQ_API_KEY, TELEGRAM_BOT_TOKEN, CHANNEL_ID
python telegrambot.py
```

Тесты: `pip install -r requirements-dev.txt && python -m pytest -q && python -m ruff check bot tests tools`

## Что закрыто из ТЗ

**Этап 1 — фильтры и рейтинг.** Термины ищутся по границам слов: «работает»,
«заработал», «работоспособность» не отбрасываются. Вакансии — точными фразами.
«Обзор», «тест», «review» не убивают профильные темы (блокировки, VPN, прокси,
сети, безопасность); отсекаются только коммерческие подборки. Все источники
участвуют в одном рейтинге: тематика 25 + польза 25 + актуальность 20 +
новизна 15 + обсуждение 10 + надёжность 5. Пороги: 80+ приоритет, 65–79
публикация, 50–64 очередь, ниже 50 отказ. Лимиты длины — по форматам
(короткая новость 350–600 больше не блокируется).

**Этап 2 — редакционная система.** Полный текст статьи (bs4, чистка меню и
рекламы) с лимитом скачиваний за прогон. Оценка материала моделью с откатом на
эвристику. Пять форматов: практическое решение, эксперимент, последствия,
разбор, инструменты. Триггер → интрига → раскрытие → польза → реакция.
Запреты: пустые вступления, кликбейт, «мы проверили», вопрос в конце каждой
публикации. Дисклеймер убран, хештегов 1–3. Самопроверка текста моделью: слабый
текст переписывается или не публикуется. Не больше двух подряд одного формата
и одной темы.

**Этап 3 — вовлечение.** Серийные рубрики с кулдауном (не подряд, не чаще
`RUBRIC_COOLDOWN_DAYS`). Одна встроенная механика на публикацию: проверка
гипотезы, сбор пользовательских случаев или продолжение популярной темы.
Опросы — только по теме материала и с предохранителями (не чаще N постов,
N в сутки), с вариантами про протокол/клиент/восстановление соединения.

**Этап 4 — аналитика.** `post_stats`: просмотры через 1, 6 и 24 часа,
пересылки, реакции, комментарии. Неизвестная метрика остаётся NULL — ноль
искажает средние. Просмотры берутся с публичной страницы поста
(t.me/<канал>/<id>), потому что Bot API их не отдаёт; закрытый канал — метрика
неизвестна. Недельный отчёт: темы, форматы против целевых долей, источники,
часы активности, систематически слабые форматы, рекомендации. Стратегию бот
сам не меняет.

**Этап 5 — очередь и управление.** `editorial_queue` + режимы:
`auto` — публикует сам; `approval` — кладёт в очередь и пишет админу;
`collect` — только собирает материалы. Режим переключается командой `/mode`
и хранится в БД (приоритетнее переменной `POSTING_MODE`).

## Команды админа

Только для ID из `ADMIN_IDS`. Процесс: `python adminbot.py`.

| Команда | Что делает |
|---|---|
| `/queue [n]` | очередь материалов с оценкой и форматом |
| `/top [n]` | лучшие найденные темы |
| `/stats` | опубликовано / отклонено / очередь / покрытие метриками |
| `/report` | отчёт за неделю |
| `/publish [id]` | сгенерировать и опубликовать материал (без id — лучший) |
| `/skip [id]` | убрать материал из очереди |
| `/why <url>` | почему материал был отклонён |
| `/settings` | текущие настройки |
| `/mode auto\|approval\|collect` | режим публикации |

## Переменные окружения

Обязательные: `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `CHANNEL_ID`.

Ключевые необязательные (полный список — в `bot/config.py` и `.env.example`):

- `POSTING_MODE` — `auto` / `approval` / `collect`
- `ADMIN_IDS`, `ADMIN_CHAT_ID` — доступ к командам и куда слать уведомления
- `CHANNEL_USERNAME` — публичный канал для сбора просмотров
- `PUBLISH_MIN_SCORE`, `QUEUE_MIN_SCORE` — пороги рейтинга
- `MAX_SAME_FORMAT_IN_ROW`, `MAX_SAME_TOPIC_IN_ROW` — баланс публикаций
- `RUBRIC_COOLDOWN_DAYS`, `POLLS_ENABLED`, `POLL_MIN_POSTS_BETWEEN`, `POLL_DAILY_LIMIT`
- `REPORT_DAYS`, `REPORT_MIN_POSTS`, `REPORT_TZ_OFFSET`
- `QUEUE_MAX_ITEMS`, `QUEUE_TTL_DAYS`, `RETENTION_DAYS`

## Схема SQLite

Старые таблицы сохранены, изменения аддитивные (ALTER TABLE + CREATE TABLE IF NOT EXISTS),
файл `posted_articles.db` продолжает работать.

```sql
posted_articles(id, url, norm_url, domain, title, title_normalized, title_words,
                title_word_signature, summary, content_hash, entities, topic, subject,
                source, posted_date, post_format, score, message_id, rubric)
rejected_urls(norm_url, title, reason, rejected_at, source)
post_stats(message_id, url, norm_url, title, source, topic, post_format, rubric, score,
           published_at, views_1h, views_6h, views_24h, forwards_24h, reactions_24h,
           comments_24h, subscribers, views_source, updated_at)
editorial_queue(id, norm_url, url, title, source, topic, post_format, rubric, score,
                payload, status, reason, created_at, decided_at, message_id)
settings(key, value)
```

Статусы очереди: `pending`, `published`, `skipped`, `expired`.

## Расписания

- `.github/workflows/post_bot.yml` — публикация, 6 раз в день (не менялось)
- `.github/workflows/stats.yml` — сбор метрик каждый час
- `.github/workflows/weekly_report.yml` — отчёт по понедельникам, 09:05 МСК
- `.github/workflows/tests.yml` — ruff + pytest на каждый push
- `.github/workflows/examples.yml` — ручной прогон примеров без публикации

## Границы

Бот не выдумывает факты, цифры и результаты тестов; не утверждает, что редакция
что-то проверяла; не публикует пустой или повреждённый текст; не отправляет пост
повторно после сбоя; не меняет редакционную стратегию без решения владельца.
Причины отказа каждого материала пишутся в `rejected_urls` и доступны через `/why`.

Документация по подаче: `docs/spec-triggers.md`. Прошлая версия README (монолит v12):
`docs/legacy-README-v12.md`.
