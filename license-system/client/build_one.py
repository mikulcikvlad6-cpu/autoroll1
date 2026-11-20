"""Сборка персонального roll.exe под конкретного пользователя.

Что делает:
  1. копирует защищённый клиент в отдельную папку;
  2. «зашивает» в код OWNER_ID (Discord ID), BUILD_ID и адрес API;
  3. по желанию прогоняет через PyArmor;
  4. собирает .exe через PyInstaller с иконкой и версией, где в свойствах файла
     видно, кому он выдан (это и есть водяной знак);
  5. кладёт рядом button.png, инструкцию и manifest.json с SHA-256.

Примеры:
    python build_one.py --discord-id 123456789012345678
    python build_one.py --discord-id 123456789012345678 --obfuscate
    python build_one.py --discord-id 123456789012345678 --api-url https://my.app
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = CLIENT_DIR.parent
SERVER_ENV = PROJECT_DIR / "server" / ".env"
TEMPLATE = CLIENT_DIR / "roll_protected.py"
MODULES = ["hwid.py", "license_client.py", "guard.py"]

INSTRUCTION = """Обязательно распакуйте архив (не запускайте прямо из ZIP).

1. Убедитесь, что roll.exe и button.png лежат в одной папке.
2. Запустите roll.exe и введите ключ из Discord.
3. Если Windows Defender пишет "Вирус": "Подробнее" -> "Выполнить в любом случае".
4. В игре откройте меню лошади и нажмите "Старт".
5. Остановить бота — клавиша F1.

Ключ привязан к вашему ПК. Для переноса на другой ПК нужен сброс HWID.
"""


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_version_file(path: Path, build_id: str, discord_id: str, version: str) -> None:
    """Ресурсы Windows: в свойствах .exe видно, кому выдан билд."""
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version.replace('.', ', ')}, 0),
    prodvers=({version.replace('.', ', ')}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'AutoRoll'),
        StringStruct('FileDescription', 'AutoRoll build {build_id} / owner {discord_id}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'roll'),
        StringStruct('LegalCopyright', 'Licensed to Discord ID {discord_id}'),
        StringStruct('OriginalFilename', 'roll.exe'),
        StringStruct('ProductName', 'AutoRoll'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('Comments', 'build={build_id} owner={discord_id}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path.write_text(content, encoding="utf-8")


def run(command: list[str], cwd: Path) -> int:
    print("$", " ".join(command))
    return subprocess.call(command, cwd=str(cwd))


def first_existing(*candidates: Path) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка персонального roll.exe")
    parser.add_argument("--discord-id", required=True, help="Discord ID получателя")
    parser.add_argument("--api-url", default="", help="Адрес сервера лицензий")
    parser.add_argument("--secret", default="", help="CLIENT_SECRET (по умолчанию из server/.env)")
    parser.add_argument("--out", default=str(PROJECT_DIR / "dist"), help="Куда класть сборки")
    parser.add_argument("--button", default="", help="Путь к button.png (обычно находится сам)")
    parser.add_argument("--icon", default="", help="Путь к icon.ico (обычно находится сам)")
    parser.add_argument("--obfuscate", action="store_true", help="Прогнать через PyArmor")
    parser.add_argument("--stage-only", action="store_true", help="Только подготовить исходники")
    parser.add_argument("--version", default="1.1.0")
    args = parser.parse_args()

    button = Path(args.button) if args.button else first_existing(
        CLIENT_DIR / "button.png",
        PROJECT_DIR / "roll" / "button.png",
        PROJECT_DIR / "button.png",
    )
    icon = Path(args.icon) if args.icon else first_existing(
        CLIENT_DIR / "icon.ico",
        PROJECT_DIR / "icon.ico",
    )

    out_dir = Path(args.out).resolve()
    discord_id = args.discord_id.strip()
    if not re.fullmatch(r"\d{15,25}", discord_id):
        print("Discord ID должен состоять из 15–25 цифр", file=sys.stderr)
        return 2

    env = read_env(SERVER_ENV)
    api_url = (args.api_url or env.get("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    secret = args.secret or env.get("CLIENT_SECRET", "")
    if not secret or secret.startswith("CHANGE_ME") or secret == "dev-client-secret-change-me":
        print(
            "CLIENT_SECRET не задан. Укажите --secret или заполните server/.env "
            "(тот же секрет должен быть на сервере).",
            file=sys.stderr,
        )
        return 2

    build_id = f"{discord_id[-6:]}-{secrets.token_hex(2).upper()}"
    label = f"owner {discord_id}"

    work = out_dir / f"_work_{discord_id}"
    final = out_dir / discord_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    final.mkdir(parents=True, exist_ok=True)

    # --- исходники -----------------------------------------------------
    source = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        '"__API_URL__"': json.dumps(api_url),
        '"__CLIENT_SECRET__"': json.dumps(secret),
        '"__OWNER_ID__"': json.dumps(discord_id),
        '"__BUILD_ID__"': json.dumps(build_id),
        '"__BUILD_LABEL__"': json.dumps(label),
    }
    for placeholder, value in replacements.items():
        source = source.replace(placeholder, value)
    (work / "roll.py").write_text(source, encoding="utf-8")

    for module in MODULES:
        shutil.copy2(CLIENT_DIR / module, work / module)
    if button is not None:
        shutil.copy2(button, work / "button.png")
    else:
        print(
            "ВНИМАНИЕ: button.png не найден. Положите его в папку client/ "
            "или рядом с roll.py — без него программа не работает.",
            file=sys.stderr,
        )

    version_file = work / "version_info.txt"
    write_version_file(version_file, build_id, discord_id, args.version)

    manifest = {
        "discord_id": discord_id,
        "build_id": build_id,
        "api_url": api_url,
        "version": args.version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if args.stage_only:
        (work / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Исходники готовы: {work}")
        return 0

    build_target = work / "roll.py"
    extra_data: list[str] = []
    data_sep = ";" if os.name == "nt" else ":"

    # --- обфускация ----------------------------------------------------
    if args.obfuscate:
        obf_dir = work / "obf"
        pyarmor = shutil.which("pyarmor")
        obf_cmd = (
            [pyarmor, "gen", "-O", str(obf_dir), str(build_target)]
            if pyarmor
            else [
                sys.executable,
                "-m",
                "pyarmor.cli",
                "gen",
                "-O",
                str(obf_dir),
                str(build_target),
            ]
        )
        code = run(obf_cmd, cwd=work)
        if code != 0:
            print(
                "PyArmor недоступен или упал. Установите: pip install pyarmor\n"
                "Соберу без обфускации — это менее защищено.",
                file=sys.stderr,
            )
        else:
            obf_script = obf_dir / "roll.py"
            if obf_script.exists():
                for module in MODULES:
                    shutil.copy2(work / module, obf_dir / module)
                for runtime in obf_dir.glob("pyarmor_runtime_*"):
                    extra_data += ["--add-data", f"{runtime}{data_sep}{runtime.name}"]
                build_target = obf_script
                (work / "obfuscated").write_text("1", encoding="utf-8")

    dist_dir = work / "dist"
    build_dir = work / "build"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        "roll",
        "--version-file",
        str(version_file),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(work),
        "--paths",
        str(build_target.parent),
    ]
    if icon is not None:
        command += ["--icon", str(icon.resolve())]
    if button is not None:
        command += ["--add-data", f"{button.resolve()}{data_sep}."]
    command += extra_data
    command.append(str(build_target))

    code = run(command, cwd=work)
    if code != 0:
        print("Сборка не удалась. Проверьте вывод PyInstaller выше.", file=sys.stderr)
        return code

    exe_src = dist_dir / "roll.exe"
    if not exe_src.exists():
        print("PyInstaller не создал roll.exe", file=sys.stderr)
        return 3

    exe_dst = final / "roll.exe"
    shutil.copy2(exe_src, exe_dst)
    if button is not None:
        shutil.copy2(button, final / "button.png")
    (final / "Инструкция.txt").write_text(INSTRUCTION, encoding="utf-8")

    manifest["sha256"] = hashlib.sha256(exe_dst.read_bytes()).hexdigest()
    manifest["size_bytes"] = exe_dst.stat().st_size
    manifest["obfuscated"] = (work / "obfuscated").exists()
    (final / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n=== ГОТОВО ===")
    print(f"EXE:      {exe_dst}")
    print(f"BUILD ID: {build_id}")
    print(f"SHA-256:  {manifest['sha256']}")
    print(f"Водяной знак: собственник Discord ID {discord_id} (см. свойства .exe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
