# Запуск Discord-бота (из папки server)
$ErrorActionPreference = "Stop"
if (-not (Test-Path ".env")) {
    throw "Нет .env — скопируйте .env.example в .env и заполните DISCORD_BOT_TOKEN."
}
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    & ".\.venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
}
& ".\.venv\Scripts\python.exe" ".\bot\bot.py"
