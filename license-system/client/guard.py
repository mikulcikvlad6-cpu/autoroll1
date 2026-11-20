"""Защитные механизмы клиента.

Здесь три независимые штуки:
  * schedule_self_delete — удаление .exe через временный .bat (kill switch);
  * TamperGuard — ловит отладчик/подмену функций и сообщает на сервер;
  * show_block_screen — блокирующие окна при бане HWID.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from license_client import LicenseClient

# Тексты блокировки. Меняйте как хотите — это просто строки в окнах.
LOCK_TEXTS = [
    "ДОСТУП ЗАБЛОКИРОВАН",
    "Это устройство в чёрном списке",
    "Лицензия аннулирована",
    "Обратитесь к администратору",
]

MAX_BLOCK_WINDOWS = 15


# --------------------------------------------------------------------------
# Самоудаление
# --------------------------------------------------------------------------
def schedule_self_delete(extra_paths: list[str] | None = None, delay: int = 2) -> None:
    """Запускает .bat, который удалит текущий .exe после выхода процесса.

    ВАЖНО: такое поведение — частая причина срабатывания антивирусов.
    В скриптовом режиме (python roll.py) ничего не удаляется.
    """
    if not getattr(sys, "frozen", False):
        return

    exe_path = Path(sys.executable).resolve()
    paths = [str(exe_path)] + [str(Path(p).resolve()) for p in (extra_paths or [])]

    lines = ["@echo off", f"timeout /t {delay} /nobreak >nul"]
    for path in paths:
        lines.append(f':wait_{abs(hash(path)) % 100000}')
        lines.append(f'del /f /q "{path}" >nul 2>&1')
        lines.append(f'if exist "{path}" (timeout /t 1 /nobreak >nul & goto wait_{abs(hash(path)) % 100000})')
    lines.append('del /f /q "%~f0" >nul 2>&1')
    script = "\r\n".join(lines) + "\r\n"

    bat_path = Path(tempfile.gettempdir()) / f"ar_{os.getpid()}.bat"
    try:
        bat_path.write_text(script, encoding="utf-8", errors="ignore")
    except OSError:
        return

    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
# Детект отладки и правок кода
# --------------------------------------------------------------------------
def debugger_present() -> bool:
    try:
        if ctypes.windll.kernel32.IsDebuggerPresent():
            return True
        present = ctypes.c_int(0)
        ctypes.windll.kernel32.CheckRemoteDebuggerPresent(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(present)
        )
        return bool(present.value)
    except Exception:
        return False


class TamperGuard:
    """Сторож: следит за целостностью функций клиента и живостью heartbeat."""

    def __init__(self, client: LicenseClient) -> None:
        self.client = client
        self.baseline = self._fingerprint()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.triggered = False

    @staticmethod
    def _fingerprint() -> str:
        parts: list[bytes] = []
        for func in (LicenseClient.verify, LicenseClient.activate, LicenseClient._post):
            code = getattr(func, "__code__", None)
            parts.append(code.co_code if code else b"")
        return hashlib.sha256(b"|".join(parts)).hexdigest()

    def check(self) -> str | None:
        if debugger_present():
            return "debugger"
        try:
            if self._fingerprint() != self.baseline:
                return "code_patched"
        except Exception:
            return "integrity_error"
        thread = self.client._thread
        if thread is not None and not thread.is_alive() and not self.client._stop.is_set():
            return "heartbeat_killed"
        return None

    def start(
        self,
        on_tamper: Callable[[str], None],
        interval: float = 8.0,
    ) -> None:
        def worker() -> None:
            while not self._stop.wait(interval):
                reason = self.check()
                if reason and not self.triggered:
                    self.triggered = True
                    try:
                        on_tamper(reason)
                    except Exception:
                        pass

        self._thread = threading.Thread(target=worker, name="tamper-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------
# Блокирующие окна
# --------------------------------------------------------------------------
def show_block_screen(title: str, message: str, count: int = 12) -> None:
    """Открывает несколько окон поверх всего. Закрытие возвращает новое окно."""
    import tkinter as tk

    count = max(1, min(count, MAX_BLOCK_WINDOWS))
    root = tk.Tk()
    root.withdraw()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    opened = 0

    def spawn(index: int) -> None:
        nonlocal opened
        if opened >= MAX_BLOCK_WINDOWS * 2:
            return
        opened += 1
        window = tk.Toplevel(root)
        window.title("AutoRoll")
        window.configure(bg="#1a0505")
        window.attributes("-topmost", True)
        window.resizable(False, False)
        x = (screen_w // 2 - 190 + (index % 4) * 60) % max(screen_w - 400, 1)
        y = (screen_h // 2 - 90 + (index % 3) * 50) % max(screen_h - 200, 1)
        window.geometry(f"380x170+{x}+{y}")

        tk.Label(
            window, text=LOCK_TEXTS[index % len(LOCK_TEXTS)],
            bg="#1a0505", fg="#ff4d4d", font=("Arial", 14, "bold"), wraplength=340,
        ).pack(pady=(18, 6))
        tk.Label(
            window, text=message, bg="#1a0505", fg="#f3b7b7",
            font=("Arial", 9), wraplength=340, justify="center",
        ).pack(pady=4)
        tk.Label(
            window, text=title, bg="#1a0505", fg="#6b2b2b", font=("Arial", 8),
        ).pack(side="bottom", pady=6)

        window.protocol("WM_DELETE_WINDOW", lambda: spawn(index + 1))

    for i in range(count):
        spawn(i)

    root.after(20000, root.destroy)
    root.mainloop()


def full_shutdown(
    client: LicenseClient,
    root=None,
    block_message: str | None = None,
    delete: bool = True,
) -> None:
    """Остановить всё: heartbeat, окна, процесс, файл."""
    try:
        client.stop_heartbeat()
    except Exception:
        pass
    if root is not None:
        try:
            root.destroy()
        except Exception:
            pass
    if block_message:
        show_block_screen("AutoRoll", block_message, count=10)
    if delete:
        schedule_self_delete()
    time.sleep(0.3)
    os._exit(0)
