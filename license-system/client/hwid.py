"""HWID — стабильный отпечаток ПК.

Собирается из MachineGuid, серийного номера системного диска и имени ПК,
затем хешируется. Пользователь не может изменить это «случайно», но
целенаправленно подделать можно — 100% защиты не существует.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import uuid

# Соль стоит менять только вместе с пересборкой всех клиентов.
_SALT = b"AutoRoll::HWID::v1"
_hwid_cache: str | None = None


def _machine_guid() -> str:
    try:
        import winreg

        access = winreg.KEY_READ
        try:
            access |= winreg.KEY_WOW64_64KEY
        except AttributeError:
            pass
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except Exception:
        return ""


def _volume_serial(root: str = "C:\\") -> str:
    try:
        volume_name = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_uint()
        max_len = ctypes.c_uint()
        flags = ctypes.c_uint()
        fs_name = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            volume_name,
            261,
            ctypes.byref(serial),
            ctypes.byref(max_len),
            ctypes.byref(flags),
            fs_name,
            261,
        )
        return f"{serial.value:08X}" if ok else ""
    except Exception:
        return ""


def get_hwid() -> str:
    global _hwid_cache
    if _hwid_cache:
        return _hwid_cache

    parts = [
        _machine_guid(),
        _volume_serial(os.environ.get("SystemDrive", "C:") + "\\"),
        platform.node(),
        f"{uuid.getnode():012X}",
    ]
    raw = "|".join(part for part in parts if part).encode("utf-8")
    digest = hashlib.sha256(_SALT + raw).hexdigest().upper()
    _hwid_cache = digest[:32]
    return _hwid_cache


if __name__ == "__main__":  # удобно проверить отпечаток вручную
    print(get_hwid())
