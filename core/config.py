import json
import os
import threading

CONFIG_DIR = "config"
CONFIG_PATH = os.path.join(CONFIG_DIR, "server.json")
_lock = threading.RLock()

DEFAULT_CONFIG = {
    "name": "Web File Sender",
    "host": "0.0.0.0",
    "port": 6789,
    "downloads_enabled": True,
    "device_speed_limits_kbps": {},
    "streaming_enabled": True,
    "password": "",
    "shares": [],
    "rules": [],
    "hidden_files": False,
    "blocked_clients": [],
    "blocked_client_reasons": {},
    "paused_clients": [],
    "pause_expiries": {},
    "appeals": [],
    "receiver_devices": {},
}

def load_config():
    with _lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = dict(DEFAULT_CONFIG)
        legacy_admin_keys = ("admin_username", "admin_password_hash", "admin_session_key")
        deprecated_keys = (*legacy_admin_keys, "download_speed_limit_kbps")
        if any(key in data for key in deprecated_keys):
            for key in deprecated_keys:
                data.pop(key, None)
            save_config(data)
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        merged["shares"] = merged.get("shares") or []
        merged["rules"] = merged.get("rules") or []
        merged["blocked_clients"] = merged.get("blocked_clients") or []
        merged["blocked_client_reasons"] = merged.get("blocked_client_reasons") or {}
        merged["paused_clients"] = merged.get("paused_clients") or []
        merged["pause_expiries"] = merged.get("pause_expiries") or {}
        merged["appeals"] = merged.get("appeals") or []
        if not isinstance(merged.get("device_speed_limits_kbps"), dict):
            merged["device_speed_limits_kbps"] = {}
        if not isinstance(merged.get("receiver_devices"), dict):
            merged["receiver_devices"] = {}
        return merged

def save_config(data):
    with _lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
