# Сборка roll.exe

## 0. Что нужно один раз

```powershell
cd client
pip install -r requirements.txt pyinstaller pyarmor
```

`button.png` и `icon.ico` положи в `client/` (скрипт ищет их и в
`../roll/button.png`, `../icon.ico`).

## 1. Обычная сборка под пользователя

```powershell
python build_one.py --discord-id 123456789012345678
```

Что происходит:

1. создаётся `dist\_work_<id>\` с копией `roll_protected.py`, `hwid.py`,
   `license_client.py`, `guard.py`, `button.png`;
2. в код подставляются `API_URL`, `CLIENT_SECRET`, `OWNER_ID`, `BUILD_ID`;
3. генерируется `version_info.txt` — в свойствах `.exe` будет
   `Licensed to Discord ID ...`;
4. PyInstaller собирает `--onefile --noconsole` с иконкой и `button.png` внутри;
5. результат: `dist\<id>\roll.exe`, `button.png`, `Инструкция.txt`,
   `manifest.json` (SHA-256, размер, build_id).

Адрес API берётся из `server/.env` (`PUBLIC_BASE_URL`), секрет — оттуда же
(`CLIENT_SECRET`). Можно передать вручную:

```powershell
python build_one.py --discord-id 123456789012345678 `
    --api-url https://autoroll.onrender.com `
    --secret "<CLIENT_SECRET>"
```

## 2. С обфускацией PyArmor

```powershell
python build_one.py --discord-id 123456789012345678 --obfuscate
```

Скрипт вызывает `pyarmor gen -O dist\_work_<id>\obf roll.py`, копирует туда
остальные модули, добавляет `pyarmor_runtime_*` в бандл и собирает `.exe` уже
из обфусцированного файла. Если PyArmor не установлен или упал — сборка
продолжится без него (в консоли будет предупреждение).

Ручной вариант, если хочется контролировать флаги:

```powershell
pyarmor gen --recursive -O obf roll.py
# затем собрать obf\roll.py через PyInstaller, добавив папку pyarmor_runtime_*
pyinstaller --onefile --noconsole --add-data "obf\pyarmor_runtime_000000;pyarmor_runtime_000000" obf\roll.py
```

## 3. Проверка билда

```powershell
# свойства файла
(Get-Item .\dist\123456789012345678\roll.exe).VersionInfo | Format-List

# хеш должен совпасть с manifest.json
Get-FileHash .\dist\123456789012345678\roll.exe -Algorithm SHA256
```

Ручной сценарий приёмки:

1. Запустить на машине без Python — должно открыться окно с вводом ключа.
2. Ввести неверный ключ — понятная ошибка.
3. Ввести ключ из `/getkey` — программа запускается, в БД появляется HWID.
4. `/admin killswitch enabled:false` — в течение 15 секунд программа
   закрывается, `.exe` исчезает.
5. Запустить заново — файла нет.

## 4. Версия и обновления

Версия задаётся `--version` (по умолчанию `1.1.0`) и попадает в свойства файла.
Сервер отдаёт `latest_version` в `/verify` — при желании добавь в клиент
сравнение и ссылку на скачивание (сейчас не блокирует работу).

## 5. Частые проблемы

| Ошибка | Причина | Решение |
|---|---|---|
| `CLIENT_SECRET не задан` | нет `server/.env` | заполни или передай `--secret` |
| `PyInstaller не создал roll.exe` | смотри лог выше | чаще всего нет `pyinstaller` в PATH |
| `.exe` мгновенно закрывается | ошибка импорта | собери с `--noconsole` выключенным: `--console` |
| Defender удаляет `.exe` | эвристика на самоудаление/клики | добавь исключение, подпиши код |
| `button.png` не найден | не скопировал | положи в `client/` |

Для отладки удобно собрать консольную версию:

```powershell
pyinstaller --onefile --console --name roll_debug --add-data "button.png;." roll.py
```
