"""AutoRoll — защищённая версия roll.py.

Отличия от обычной версии:
  * при запуске нужен ключ, проверка идёт через ваш сервер;
  * HWID проверяется на сервере, забаненные ПК блокируются;
  * каждые 12 секунд клиент спрашивает сервер: «работать можно?»;
  * если сервер выключил приложение — процесс останавливается и .exe удаляется;
  * при попытке отладки/подмены кода клиент пишет сигнал в Discord-канал событий
    и переходит в «режим деградации» (клик раз в 5 секунд).

Значения ниже подставляются автоматически скриптом build_one.py при сборке
персонального .exe (OWNER_ID — «водяной знак»).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

import tkinter as tk
from tkinter import messagebox

# --- параметры сборки (не трогать вручную) ---------------------------------
API_URL = "__API_URL__"
CLIENT_SECRET = "__CLIENT_SECRET__"
OWNER_ID = "__OWNER_ID__"
BUILD_ID = "__BUILD_ID__"
BUILD_LABEL = "__BUILD_LABEL__"
# ---------------------------------------------------------------------------

APP_TITLE = "Okishima Auto Roller"
APP_VERSION = "1.1.0"
OFFLINE_GRACE_HOURS = 24
HEARTBEAT_SECONDS = 12

try:
    import cv2
    import numpy as np
    import pyautogui

    VISION_OK = True
except Exception:  # зависимости могут отсутствовать в dev-режиме
    VISION_OK = False

try:
    import keyboard

    KEYBOARD_OK = True
except Exception:
    KEYBOARD_OK = False

try:
    import winsound

    SOUND_OK = True
except Exception:
    SOUND_OK = False

from guard import TamperGuard, debugger_present, full_shutdown
from hwid import get_hwid
from license_client import LicenseClient, LicenseError, LicenseNetworkError

CONFIDENCE = 0.7
RED_PIXEL_THRESHOLD = 30

if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BUTTON_PATH = os.path.join(SCRIPT_DIR, "button.png")


# --------------------------------------------------------------------------
# Диалог ввода ключа
# --------------------------------------------------------------------------
class KeyDialog:
    def __init__(self, parent: tk.Tk, error: str = "") -> None:
        self.result: str | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Активация AutoRoll")
        self.window.configure(bg="#141414")
        self.window.geometry("420x210")
        self.window.resizable(False, False)
        self.window.attributes("-topmost", True)
        self.window.grab_set()

        tk.Label(
            self.window, text="Введите ключ доступа",
            bg="#141414", fg="white", font=("Arial", 14, "bold"),
        ).pack(pady=(18, 4))
        tk.Label(
            self.window,
            text=error or "Ключ выдаётся командой /getkey в Discord",
            bg="#141414", fg="#f87171" if error else "#8b9bb0",
            font=("Arial", 9), wraplength=380, justify="center",
        ).pack(pady=(0, 8))

        self.entry = tk.Entry(
            self.window, font=("Consolas", 16), justify="center",
            bg="#0b0f14", fg="#6ee7b7", insertbackground="#6ee7b7",
            relief="flat", highlightthickness=1, highlightbackground="#2b3d50",
        )
        self.entry.pack(fill="x", padx=30, ipady=8)
        self.entry.focus_set()

        buttons = tk.Frame(self.window, bg="#141414")
        buttons.pack(pady=16)
        tk.Button(
            buttons, text="Активировать", command=self._ok,
            bg="#10b981", fg="#052e1f", font=("Arial", 10, "bold"),
            relief="flat", padx=18, pady=6,
        ).pack(side="left", padx=6)
        tk.Button(
            buttons, text="Выход", command=self._cancel,
            bg="#374151", fg="white", font=("Arial", 10),
            relief="flat", padx=18, pady=6,
        ).pack(side="left", padx=6)

        self.window.bind("<Return>", lambda _event: self._ok())
        self.window.bind("<Escape>", lambda _event: self._cancel())
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

    def _ok(self) -> None:
        value = self.entry.get().strip()
        if not value:
            return
        self.result = value
        self.window.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()


# --------------------------------------------------------------------------
# Лицензионный шлюз
# --------------------------------------------------------------------------
class LicenseGate:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.hwid = get_hwid()
        self.client = LicenseClient(
            api_base=API_URL,
            secret=CLIENT_SECRET,
            hwid=self.hwid,
            owner_id=None if OWNER_ID in ("", "0", "__OWNER_ID__") else OWNER_ID,
        )
        self.click_delay_ms = 2000
        self.degraded = False
        self.tamper_reported = False
        self.shut_down = False
        self.guard = TamperGuard(self.client)
        self.app = None

    # ------------------------------------------------------------- проверка
    def ensure_license(self) -> bool:
        if debugger_present():
            self.client.report_tamper("debugger", "debugger at startup")
            self._degrade("Обнаружен отладчик")

        if self.client.token:
            try:
                result = self.client.verify()
            except LicenseNetworkError:
                if self.client.offline_allowed(OFFLINE_GRACE_HOURS):
                    return True
                messagebox.showerror(
                    APP_TITLE,
                    "Нет связи с сервером лицензий.\n"
                    "Проверьте интернет и запустите программу снова.",
                )
                return False
            except LicenseError as exc:
                if exc.code == "BAD_SIGNATURE":
                    self._report_and_degrade("bad_signature", exc.message)
                self.client.clear_cache()
            else:
                if not result.get("enabled"):
                    self._handle_refusal(result, blocking=True)
                    return False
                self._apply_result(result)
                self._start_watchdog()
                return True

        return self._ask_key()

    def _ask_key(self) -> bool:
        error = ""
        for attempt in range(5):
            dialog = KeyDialog(self.root, error)
            self.root.wait_window(dialog.window)
            if not dialog.result:
                return False
            try:
                result = self.client.activate(dialog.result)
            except LicenseNetworkError:
                error = "Сервер недоступен. Проверьте интернет."
                continue
            except LicenseError as exc:
                if exc.code == "HWID_BANNED":
                    self._handle_refusal(
                        {"code": "HWID_BANNED", "message": exc.message, "kill": True},
                        blocking=True,
                    )
                    return False
                error = exc.message
                continue
            self._apply_result(result)
            self._start_watchdog()
            return True
        return False

    def _apply_result(self, result: dict) -> None:
        try:
            self.click_delay_ms = int(result.get("click_delay_ms") or 2000)
        except (TypeError, ValueError):
            self.click_delay_ms = 2000
        if self.app is not None and not self.degraded:
            self.app.click_delay_ms = self.click_delay_ms

    # -------------------------------------------------------------- отказы
    def _handle_refusal(self, result: dict, blocking: bool = False) -> None:
        """Вызывается только из главного потока (Tk)."""
        if self.shut_down:
            return
        self.shut_down = True
        code = result.get("code", "")
        message = result.get("message") or "Доступ запрещён."

        if code == "HWID_BANNED":
            full_shutdown(
                self.client, self.root, "Устройство заблокировано.", delete=True
            )
            return
        if code in ("DISABLED_BY_ADMIN", "KEY_REVOKED", "BAD_TOKEN", "TOKEN_HWID_MISMATCH"):
            full_shutdown(
                self.client, self.root, message, delete=bool(result.get("kill", False))
            )
            return

        messagebox.showerror(APP_TITLE, message)
        self._shutdown(delete=False)

    def _shutdown(self, delete: bool) -> None:
        try:
            self.client.stop_heartbeat()
            self.guard.stop()
        finally:
            full_shutdown(self.client, self.root, None, delete=delete)

    # ------------------------------------------------------------- сторожа
    def _ui(self, callback) -> None:
        """Планирование в главном потоке Tk с защитой от уничтоженного окна."""
        try:
            self.root.after(0, callback)
        except Exception:
            pass

    def _start_watchdog(self) -> None:
        self.client.start_heartbeat(
            on_result=lambda result: self._ui(lambda: self._on_heartbeat(result)),
            on_network_failure=lambda exc: self._ui(lambda: self._on_network_error(exc)),
            interval=HEARTBEAT_SECONDS,
        )
        self.guard.start(
            on_tamper=lambda reason: self._ui(
                lambda: self._report_and_degrade(reason, "guard triggered")
            )
        )

    def _on_heartbeat(self, result: dict) -> None:
        if self.shut_down:
            return
        if result.get("enabled"):
            self._apply_result(result)
            return
        if result.get("code") == "BAD_SIGNATURE":
            self._report_and_degrade("bad_signature", "response signature mismatch")
            return
        self._handle_refusal(result)

    def _on_network_error(self, exc: Exception) -> None:
        if self.shut_down:
            return
        if not self.client.offline_allowed(OFFLINE_GRACE_HOURS):
            self._handle_refusal(
                {
                    "code": "OFFLINE",
                    "message": "Нет связи с сервером лицензий. Запустите позже.",
                    "kill": False,
                }
            )
            return
        if self.app is not None:
            self.app.set_status("Нет связи с сервером (оффлайн-режим)")

    # --------------------------------------------------------- деградация
    def _report_and_degrade(self, kind: str, detail: str = "") -> None:
        self._degrade("Обнаружено вмешательство в защиту")
        if self.tamper_reported:
            return
        self.tamper_reported = True
        threading.Thread(
            target=lambda: self.client.report_tamper(kind, detail), daemon=True
        ).start()

    def _degrade(self, reason: str) -> None:
        if self.degraded:
            return
        self.degraded = True
        self.click_delay_ms = 5000
        if self.app is not None:
            self.app.click_delay_ms = 5000
            self.app.set_status("РЕЖИМ ДЕГРАДАЦИИ: клик раз в 5 секунд")
        messagebox.showwarning(
            APP_TITLE,
            f"{reason}.\n\n"
            "Программа продолжит работу в режиме деградации: один клик в 5 секунд.\n"
            "Информация отправлена разработчику.",
        )


# --------------------------------------------------------------------------
# Само приложение
# --------------------------------------------------------------------------
class AutoRollApp:
    def __init__(self, root: tk.Tk, gate: LicenseGate) -> None:
        self.root = root
        self.gate = gate
        gate.app = self
        self.click_delay_ms = gate.click_delay_ms

        self.root.title(f"{APP_TITLE} · {BUILD_LABEL}")
        self.root.geometry("320x270")
        self.root.configure(bg="#1e1e1e")
        self.root.resizable(False, False)

        self.is_running = False
        self.bot_thread: threading.Thread | None = None

        if not os.path.exists(BUTTON_PATH):
            messagebox.showerror(
                "Ошибка", "Файл button.png не найден!\nПоложите его рядом с программой."
            )
            self.root.destroy()
            return

        self.setup_ui()
        if KEYBOARD_OK:
            try:
                keyboard.add_hotkey("f1", lambda: self.root.after(0, self.stop_bot))
            except Exception:
                pass

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_ui(self) -> None:
        tk.Label(
            self.root, text="Auto Roller", font=("Arial", 16, "bold"),
            bg="#1e1e1e", fg="white",
        ).pack(pady=(10, 2))

        tk.Label(
            self.root, text=f"build {BUILD_ID} · v{APP_VERSION}",
            font=("Arial", 7), bg="#1e1e1e", fg="#4b5563",
        ).pack()

        self.status_var = tk.StringVar(value="Готов к запуску")
        tk.Label(
            self.root, textvariable=self.status_var, font=("Arial", 9),
            bg="#1e1e1e", fg="#a0a0a0", wraplength=290,
        ).pack(pady=6)

        tk.Label(
            self.root,
            text="1. Откройте меню лошади\n2. Нажмите СТАРТ\n3. Для СТОПА нажмите F1",
            font=("Arial", 9), bg="#1e1e1e", fg="#6ee7b7", justify="center",
        ).pack(pady=4)

        self.start_btn = tk.Button(
            self.root, text="СТАРТ", font=("Arial", 12, "bold"),
            bg="#10b981", fg="white", width=15, relief="flat", command=self.start_bot,
        )
        self.start_btn.pack(pady=4)

        self.stop_btn = tk.Button(
            self.root, text="СТОП (F1)", font=("Arial", 12, "bold"),
            bg="#ef4444", fg="white", width=15, relief="flat",
            state=tk.DISABLED, command=self.stop_bot,
        )
        self.stop_btn.pack(pady=4)

    def set_status(self, text: str) -> None:
        try:
            self.status_var.set(text)
        except Exception:
            pass

    # ------------------------------------------------------------------ UI
    def start_bot(self) -> None:
        if self.is_running:
            return
        if not VISION_OK:
            messagebox.showerror(
                "Ошибка",
                "Не найдены библиотеки: pyautogui / opencv-python / numpy.\n"
                "Установите их: pip install -r requirements.txt",
            )
            return
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED, bg="#374151")
        self.stop_btn.config(state=tk.NORMAL, bg="#ef4444")
        self.set_status("Статус: РАБОТАЕТ")
        self.bot_thread = threading.Thread(target=self.bot_loop, daemon=True)
        self.bot_thread.start()

    def stop_bot(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        try:
            self.start_btn.config(state=tk.NORMAL, bg="#10b981")
            self.stop_btn.config(state=tk.DISABLED, bg="#374151")
        except tk.TclError:
            return
        self.set_status("Статус: ОСТАНОВЛЕН")
        if SOUND_OK:
            try:
                winsound.Beep(500, 300)
            except Exception:
                pass

    def on_close(self) -> None:
        self.is_running = False
        self.gate.client.stop_heartbeat()
        self.root.destroy()

    # ------------------------------------------------------------- рабочий цикл
    def bot_loop(self) -> None:
        time.sleep(3)
        while self.is_running:
            try:
                if not self.check_for_red():
                    self.set_status("Статус: 🎉 НАЙДЕНА!")
                    if SOUND_OK:
                        for _ in range(3):
                            winsound.Beep(1000, 500)
                            time.sleep(0.2)
                    self.root.after(
                        0, lambda: messagebox.showinfo("УСПЕХ", "Лошадь лучше! Иди покупай!")
                    )
                    self.root.after(0, self.stop_bot)
                    break

                self.set_status("Статус: Кручу (есть минусы)...")
                coords = self.find_button()
                if coords:
                    pyautogui.click(coords[0], coords[1])
                else:
                    self.set_status("Статус: Кнопка не найдена")

                # Задержка приходит с сервера; в режиме деградации — 5 секунд.
                deadline = time.monotonic() + max(self.click_delay_ms, 200) / 1000.0
                while self.is_running and time.monotonic() < deadline:
                    time.sleep(0.05)
            except Exception:
                self.set_status("Статус: ошибка, остановлено")
                traceback.print_exc()
                self.root.after(0, self.stop_bot)
                break

    # ---------------------------------------------------------------- зрение
    def find_button(self):
        screenshot = pyautogui.screenshot()
        screen_np = np.array(screenshot)
        screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
        template = cv2.imread(BUTTON_PATH)
        if template is None:
            return None
        result = cv2.matchTemplate(screen_cv, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        if max_val >= CONFIDENCE:
            height, width = template.shape[:2]
            return (max_loc[0] + width // 2, max_loc[1] + height // 2)
        return None

    def check_for_red(self) -> bool:
        screen_width, screen_height = pyautogui.size()
        region = (
            int(screen_width * 0.05),
            int(screen_height * 0.4),
            int(screen_width * 0.4),
            int(screen_height * 0.8),
        )
        screenshot = pyautogui.screenshot(region=region)
        screen_np = np.array(screenshot)
        hsv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2HSV)
        lower_red1 = np.array([0, 100, 100])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([170, 100, 100])
        upper_red2 = np.array([180, 255, 255])
        mask = cv2.inRange(hsv, lower_red1, upper_red1) + cv2.inRange(
            hsv, lower_red2, upper_red2
        )
        return cv2.countNonZero(mask) > RED_PIXEL_THRESHOLD


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    root.title(APP_TITLE)

    gate = LicenseGate(root)
    try:
        allowed = gate.ensure_license()
    except Exception:
        traceback.print_exc()
        messagebox.showerror(APP_TITLE, "Ошибка запуска. Подробности в консоли.")
        return

    if not allowed:
        try:
            root.destroy()
        except Exception:
            pass
        return

    root.deiconify()
    AutoRollApp(root, gate)
    root.mainloop()


if __name__ == "__main__":
    main()
