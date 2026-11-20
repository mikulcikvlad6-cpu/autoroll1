"""Клиент лицензирования: активация, проверка, heartbeat, сигналы о взломе.

Только стандартная библиотека — меньше зависимостей в .exe и меньше поводов
для ложных срабатываний антивируса.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "AutoRoll/1.0"


class LicenseError(Exception):
    """Сервер ответил отказом."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class LicenseNetworkError(Exception):
    """Сервер недоступен — это не отзыв лицензии."""


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sign(data: Any, secret: str) -> str:
    return hmac.new(secret.encode(), _canonical(data), hashlib.sha256).hexdigest()


def _cache_dir() -> Path:
    candidates = [
        Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "AutoRoll",
        Path.home() / ".autoroll",
    ]
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    import tempfile

    fallback = Path(tempfile.gettempdir()) / "AutoRoll"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _mask(data: bytes, secret: str, hwid: str) -> bytes:
    the_stream = hmac.new(secret.encode(), hwid.encode(), hashlib.sha256).digest()
    return bytes(byte ^ the_stream[i % len(the_stream)] for i, byte in enumerate(data))


class LicenseClient:
    def __init__(
        self,
        api_base: str,
        secret: str,
        hwid: str,
        owner_id: str | None = None,
        timeout: float = 10.0,
        cache_file: str | Path | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.secret = secret
        self.hwid = hwid
        self.owner_id = owner_id
        self.timeout = timeout
        self.cache_path = Path(cache_file) if cache_file else _cache_dir() / "license.dat"
        self.key: str | None = None
        self.token: str | None = None
        self.expires_at: int = 0
        self.last_ok: float = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_cache()

    # ------------------------------------------------------------------ сеть
    def _headers(self, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = _sign(
            {"ts": timestamp, "body": hashlib.sha256(body).hexdigest()}, self.secret
        )
        return {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def _verify_signature(self, payload: dict) -> bool:
        signature = payload.get("sig")
        if not signature:
            return False
        body = {k: v for k, v in payload.items() if k not in ("sig", "server_time")}
        return hmac.compare_digest(_sign(body, self.secret), signature)

    def _post(self, path: str, payload: dict) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=body,
            headers=self._headers(body),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            code, message = "HTTP_ERROR", f"Сервер вернул {exc.code}"
            try:
                detail = json.loads(raw).get("detail")
                if isinstance(detail, dict):
                    code = detail.get("code", code)
                    message = detail.get("message", message)
            except Exception:
                pass
            raise LicenseError(code, message, exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LicenseNetworkError(str(exc)) from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise LicenseNetworkError("Некорректный ответ сервера") from None

        if not self._verify_signature(data):
            raise LicenseError("BAD_SIGNATURE", "Ответ сервера не прошёл проверку подписи.")
        return data

    # ------------------------------------------------------------- хранилище
    def _load_cache(self) -> None:
        try:
            raw = self.cache_path.read_bytes()
            decoded = _mask(base64.b64decode(raw), self.secret, self.hwid)
            data = json.loads(decoded)
        except Exception:
            return
        if data.get("hwid") != self.hwid:
            return
        self.key = data.get("key")
        self.token = data.get("token")
        self.expires_at = int(data.get("expires_at") or 0)
        self.last_ok = float(data.get("last_ok") or 0)

    def _save_cache(self) -> None:
        data = {
            "hwid": self.hwid,
            "key": self.key,
            "token": self.token,
            "expires_at": self.expires_at,
            "last_ok": self.last_ok,
        }
        encoded = base64.b64encode(
            _mask(json.dumps(data).encode(), self.secret, self.hwid)
        )
        try:
            self.cache_path.write_bytes(encoded)
        except OSError:
            pass

    def clear_cache(self) -> None:
        self.key = self.token = None
        self.expires_at = 0
        self.last_ok = 0.0
        try:
            self.cache_path.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------------------------------------------------------------- методы
    def activate(self, key: str) -> dict[str, Any]:
        data = self._post(
            "/api/v1/activate",
            {
                "key": key.strip(),
                "hwid": self.hwid,
                "discord_id": self.owner_id,
                "client_version": USER_AGENT.split("/")[1],
            },
        )
        with self._lock:
            self.key = data.get("license", {}).get("key") or key.strip()
            self.token = data["token"]
            self.expires_at = int(data.get("expires_at") or 0)
            self.last_ok = time.time()
            self._save_cache()
        return data

    def verify(self) -> dict[str, Any]:
        if not self.token:
            raise LicenseError("NO_TOKEN", "Нет сохранённой активации.")
        data = self._post(
            "/api/v1/verify",
            {
                "token": self.token,
                "hwid": self.hwid,
                "client_version": USER_AGENT.split("/")[1],
            },
        )
        if data.get("ok"):
            with self._lock:
                self.last_ok = time.time()
                self._save_cache()
        return data

    def report_tamper(self, kind: str, detail: str = "") -> None:
        try:
            self._post(
                "/api/v1/tamper",
                {
                    "hwid": self.hwid,
                    "kind": kind,
                    "detail": detail[:400],
                    "token": self.token,
                    "discord_id": self.owner_id,
                },
            )
        except Exception:
            # Сигнал — не критичная операция, падать из-за него нельзя.
            pass

    # ------------------------------------------------------------- heartbeat
    def offline_allowed(self, grace_hours: float) -> bool:
        if self.last_ok <= 0:
            return False
        return (time.time() - self.last_ok) < grace_hours * 3600

    def start_heartbeat(
        self,
        on_result: Callable[[dict[str, Any]], None],
        on_network_failure: Callable[[Exception], None] | None = None,
        interval: float = 12.0,
    ) -> None:
        """Каждые interval секунд дергает /verify. Останавливается через stop()."""
        self._stop.clear()

        def worker() -> None:
            while not self._stop.wait(interval):
                try:
                    result = self.verify()
                except LicenseError as exc:
                    on_result(
                        {
                            "ok": False,
                            "enabled": False,
                            "code": exc.code,
                            "message": exc.message,
                            "kill": exc.code in (
                                "BAD_TOKEN",
                                "KEY_REVOKED",
                                "HWID_BANNED",
                                "TOKEN_HWID_MISMATCH",
                                "BAD_SIGNATURE",
                            ),
                        }
                    )
                    continue
                except LicenseNetworkError as exc:
                    if on_network_failure:
                        on_network_failure(exc)
                    continue
                on_result(result)

        self._thread = threading.Thread(target=worker, name="license-heartbeat", daemon=True)
        self._thread.start()

    def stop_heartbeat(self) -> None:
        self._stop.set()
