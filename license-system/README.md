# AutoRoll License System

Полный комплект «крепости и пульта управления» для `roll.exe`:

| Что | Где | Статус |
|---|---|---|
| API лицензий (FastAPI + PostgreSQL/SQLite) | `server/app` | готово, 12 тестов |
| Discord-бот (`/getkey`, админ-команды, kill switch) | `server/bot` | готово |
| OAuth2-выдача ключей + ЛС | `server/app/routes/oauth.py` | готово |
| Защищённый клиент (`roll.py`) | `client/roll_protected.py` | готово |
| HWID, подпись ответов, анти-тампер | `client/license_client.py`, `client/guard.py` | готово |
| Kill switch + самоудаление | `client/guard.py` | готово |
| Сборка персональных `.exe` с водяным знаком | `client/build_one.py` | готово |
| Обфускация PyArmor | `client/build_one.py --obfuscate` | готово |
| Деплой Render / Railway / Docker | `server/Dockerfile`, `render.yaml` | готово |

Подробная инструкция «что делать руками» — в файле **[ЧТО_ДЕЛАТЬ_МНЕ.md](ЧТО_ДЕЛАТЬ_МНЕ.md)**.

---

## Как это работает

```
                     ┌──────────────────────────────┐
  /getkey  ─────────▶│  Discord-бот (discord.py)    │
  OAuth2   ─────────▶│  выдаёт ключ, пишет в ЛС     │
                     └──────────────┬───────────────┘
                                    │ одна и та же БД
  roll.exe ─────────────────────────▼──────────────────────┐
     │  POST /api/v1/activate  {key, hwid}                  │
     │  POST /api/v1/verify    {token, hwid}   каждые 12 с  │  FastAPI
     │  POST /api/v1/tamper    {kind, hwid}                 │  + PostgreSQL
     ◀── подписанный HMAC ответ: enabled / kill / delay ────┘
```

1. Пользователь жмёт `/getkey` → OAuth2 → ключ `LARP-XXXXXX` в ЛС.
2. Первый запуск: клиент отправляет ключ + HWID → сервер привязывает ключ к ПК.
3. Каждые 12 секунд клиент спрашивает `verify`. Ответ подписан `CLIENT_SECRET`,
   поэтому «подделать сервер» простым hosts-фиксом не получится.
4. `app_enabled = 0` (команда бота, админ-API или `manage.py off`) → клиент
   в течение 12 секунд останавливается и удаляет свой `.exe`.
5. HWID в чёрном списке → блокирующие окна + самоудаление.
6. Отладчик / подмена функций → сигнал в `/admin/events` + режим деградации
   (один клик в 5 секунд).

## Структура

```
autoroll-license/
├── ЧТО_ДЕЛАТЬ_МНЕ.md       ← чек-лист для тебя
├── README.md
├── docs/
│   ├── DEPLOY.md           ← Render / Railway / VPS
│   ├── BUILD.md            ← PyInstaller + PyArmor
│   └── SECURITY.md         ← честно о границах защиты
├── server/
│   ├── app/                ← FastAPI
│   │   ├── main.py  config.py  database.py  models.py
│   │   ├── security.py     ← HMAC-подписи и токены
│   │   ├── services.py     ← вся бизнес-логика (её же использует бот)
│   │   ├── discord_api.py  ← ЛС и OAuth2 через REST
│   │   └── routes/         ← public.py, admin.py, oauth.py
│   ├── bot/bot.py          ← Discord-бот
│   ├── manage.py           ← CLI: выдать/отозвать/бан/kill switch
│   ├── tests/              ← pytest, 12 сценариев
│   ├── Dockerfile  render.yaml  Procfile  .env.example
│   └── requirements.txt
├── client/
│   ├── roll_protected.py   ← новый roll.py (шаблон с плейсхолдерами)
│   ├── license_client.py   ← активация/проверка/heartbeat
│   ├── hwid.py             ← отпечаток ПК
│   ├── guard.py            ← анти-тампер, блок-окна, самоудаление
│   ├── build_one.py        ← персональная сборка .exe
│   └── requirements.txt
└── tools/smoke_client.py   ← живой e2e-тест против сервера
```

## Быстрый старт (локально, 5 минут)

```powershell
cd server
Copy-Item .env.example .env
# открой .env и заполни CLIENT_SECRET, ADMIN_API_KEY (можно сгенерировать)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# терминал 1 — API
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# терминал 2 — бот (после того как вставишь DISCORD_BOT_TOKEN)
.\.venv\Scripts\python bot\bot.py

# выдать себе ключ без Discord
.\.venv\Scripts\python manage.py issue <твой_discord_id>
```

Проверка, что всё живо:

```powershell
.\.venv\Scripts\python ..\tools\smoke_client.py `
    --api http://127.0.0.1:8000 --secret <CLIENT_SECRET> --admin <ADMIN_API_KEY>
```

## Тесты

```powershell
cd server
python -m pytest tests -q -p no:cacheprovider
# 12 passed
```

## Сборка экзешника

```powershell
cd client
pip install -r requirements.txt pyinstaller pyarmor
python build_one.py --discord-id 123456789012345678 --obfuscate
# результат: ..\dist\123456789012345678\roll.exe
```

В свойствах файла (правая кнопка → Свойства → Подробно) будет
`Licensed to Discord ID 123456789012345678` — это водяной знак.

## Важно про антивирусы

Самоудаление `.exe` и эмуляция кликов — типовые эвристики троянов/кликеров.
Defender будет ругаться. При сборке имеет смысл:
* добавить `--noconsole` (уже есть) и подписать `.exe` сертификатом (если появится);
* заранее предупредить пользователей в инструкции;
* не добавлять в клиент ничего, что трогает чужие файлы.

Подробнее — `docs/SECURITY.md`.
