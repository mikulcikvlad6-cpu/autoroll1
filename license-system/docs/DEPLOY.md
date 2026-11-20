# Деплой сервера

## Переменные окружения (одинаковые для всех платформ)

| Переменная | Обязательно | Пример |
|---|---|---|
| `DATABASE_URL` | да (прод) | `postgresql://user:pass@host:5432/db` |
| `CLIENT_SECRET` | да | 48+ случайных символов |
| `ADMIN_API_KEY` | да | другие 48+ символов |
| `DISCORD_CLIENT_ID` | да | `1234567890123456789` |
| `DISCORD_CLIENT_SECRET` | да | из портала |
| `DISCORD_BOT_TOKEN` | да (для бота/ЛС) | `MTIz...` |
| `PUBLIC_BASE_URL` | да | `https://autoroll.example.com` |
| `DISCORD_REDIRECT_URI` | да | `https://autoroll.example.com/oauth/callback` |
| `OWNER_IDS` | да | `123456789012345678` |
| `DISCORD_GUILD_ID` | желательно | твой сервер — мгновенный синк команд |
| `ADMIN_ROLE_ID` | нет | роль модератора |
| `KEY_DELIVERY` | нет | `oauth` (по умолчанию) или `direct` |
| `CLIENT_SIGN_REQUIRED` | прод: `1` | подпись запросов клиента |
| `AUTO_BAN_ON_TAMPER` | нет | `1` = автобан при сигнале |
| `OFFLINE_GRACE_HOURS` | нет | `24` |

`DATABASE_URL` вида `postgres://...` (как отдаёт Render) сервер сам приводит
к `postgresql+psycopg://...`.

---

## Render

1. GitHub-репозиторий с папкой `server/` и `render.yaml` в корне.
2. Dashboard → **New → Blueprint** → репозиторий → Apply.
3. Blueprint создаст web-сервис (Docker) и PostgreSQL, `DATABASE_URL` подставит сам.
4. Остальные переменные — в **Environment** сервиса, затем **Manual Deploy →
   Deploy latest commit**.
5. `healthCheckPath` уже прописан: `/api/v1/health`.

**Ограничения free-плана:**
* web-сервис засыпает через 15 минут без запросов — первый запрос после сна
  идёт ~30 секунд, клиент это переживёт (таймаут 10 с → оффлайн-грейс);
* PostgreSQL free удаляется через 30 дней. Для постоянной работы — платный
  инстанс ($7) или Railway/VPS.

**Бот на Render free держать нельзя** (засыпает). Варианты:
* платный инстанс Render;
* Railway (не засыпает, пока есть активность);
* VPS — самый предсказуемый вариант.

---

## Railway

```bash
# из корня репозитория
railway login
railway init
railway add --database postgres
railway up
railway domain          # получить публичный адрес
```
В панели: **Variables** → добавить всё из таблицы; `DATABASE_URL` взять из
переменных Postgres (`${{Postgres.DATABASE_URL}}`).
Start command можно оставить из `Procfile` (`web: uvicorn ...`), либо Railway
возьмёт `Dockerfile`.

Отдельный сервис для бота:
* **New → Empty Service → Deploy from repo**, root `server/`,
  start command `python bot/bot.py`, те же переменные.

---

## VPS (Ubuntu, $5)

```bash
sudo apt update && sudo apt install -y docker.io
git clone <repo> && cd <repo>/server
cp .env.example .env && nano .env

sudo docker build -t autoroll .
sudo docker run -d --restart=always --name autoroll \
  --env-file .env -p 127.0.0.1:8000:8000 autoroll

sudo docker run -d --restart=always --name autoroll-bot \
  --env-file .env autoroll python bot/bot.py
```

HTTPS через Caddy (2 строки конфига):

```
autoroll.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

 Затем `PUBLIC_BASE_URL=https://autoroll.example.com`,
`DISCORD_REDIRECT_URI=https://autoroll.example.com/oauth/callback`,
`sudo docker restart autoroll autoroll-bot`.

---

## Обновление

```bash
git pull
sudo docker build -t autoroll . && sudo docker restart autoroll
```
Схема БД создаётся автоматически при старте (`init_db`). Если меняешь модели —
удалять таблицы не нужно, новые колонки SQLAlchemy не добавит: для этого
проекта проще пересоздать БД или добавить `ALTER TABLE` вручную.

## Резервная копия

```bash
# Postgres
pg_dump "$DATABASE_URL" > backup_$(date +%F).sql
# SQLite
cp server/data/roll.db backup_roll_$(date +%F).db
```

## Мониторинг

* `/api/v1/health` — аптайм.
* `/admin/overview` (с `X-Admin-Key`) — ключи, баны, tamper-события.
* Render/Railway logs — ошибки FastAPI.
