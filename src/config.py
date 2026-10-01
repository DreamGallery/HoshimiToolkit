import os
import hashlib
import configparser
import json
from pathlib import Path


_BASE_PATH = os.path.abspath(os.path.dirname(__file__) + os.path.sep + "..")

config = configparser.ConfigParser(comment_prefixes=("#", ";", "/"), allow_no_value=True)
config.optionxform = lambda option: option
# Keep checked-in defaults separate from each operator's local configuration.
config.read(os.path.join(_BASE_PATH, "config.example.ini"), encoding="utf-8")
config.read(os.environ.get("IDOLY_TOOLKIT_CONFIG", os.path.join(_BASE_PATH, "config.ini")),
            encoding="utf-8")

_settings_file = Path(os.environ.get("IDOLY_OCTO_SETTINGS_FILE",
                                    os.path.join(_BASE_PATH, "cache", "octo-settings.json")))
_captured = json.loads(_settings_file.read_text(encoding="utf-8")) if _settings_file.is_file() else {}


def setting(section: str, name: str, default: str = "") -> str:
    """Environment variables keep device-specific Octo values out of config.ini."""
    captured_name = {"AES_PASSPHRASE": "a"}.get(name, name.lower())
    value = os.environ.get(f"IDOLY_OCTO_{name}")
    if value is None:
        value = _captured.get(captured_name)
    if value is None:
        value = config.get(section, name, fallback=default)
    return str(value).strip()

# Decryption settings
FILE_KEY = bytes.fromhex(os.environ.get("IDOLY_FILE_KEY", config.get("Decryption settings", "FILE_KEY", fallback="")))
FILE_IV = bytes.fromhex(os.environ.get("IDOLY_FILE_IV", config.get("Decryption settings", "FILE_IV", fallback="")))

# The API uses a different envelope from the on-device octocacheevai file.
API_URL = setting("Octo API settings", "URL")
API_APP_ID = setting("Octo API settings", "APP_ID")
API_VERSION = setting("Octo API settings", "VERSION")
API_CLIENT_SECRET_KEY = setting("Octo API settings", "CLIENT_SECRET_KEY")
API_AES_PASSPHRASE = setting("Octo API settings", "AES_PASSPHRASE")
API_AES_KEY_HEX = setting("Octo API settings", "AES_KEY_HEX")


def api_aes_key() -> bytes:
    if API_AES_KEY_HEX:
        key = bytes.fromhex(API_AES_KEY_HEX)
        if len(key) not in (16, 24, 32):
            raise ValueError("IDOLY_OCTO_AES_KEY_HEX must be a 16/24/32-byte AES key")
        return key
    if API_AES_PASSPHRASE:
        return hashlib.sha256(API_AES_PASSPHRASE.encode("utf-8")).digest()
    raise ValueError("Octo API AES key is missing; set IDOLY_OCTO_AES_PASSPHRASE or IDOLY_OCTO_AES_KEY_HEX")


def validate_api_settings() -> None:
    missing = [name for name, value in (
        ("URL", API_URL), ("APP_ID", API_APP_ID), ("VERSION", API_VERSION),
        ("CLIENT_SECRET_KEY", API_CLIENT_SECRET_KEY)
    ) if not value]
    if missing:
        raise ValueError("Missing Octo API settings: " + ", ".join(missing))
    if not API_APP_ID.isdecimal() or not API_VERSION.isdecimal():
        raise ValueError("Octo APP_ID and VERSION must be decimal numbers")
    api_aes_key()

# Download settings
UPDATE_FLAG = config.getboolean("Download settings", "UPDATE_FLAG")
MAX_RETRIES = config.getint("Download settings", "MAX_RETRIES")

# Path settings
def data_path(name: str) -> str:
    value = Path(os.environ.get(f"IDOLY_{name}", config.get("Path settings", name))).expanduser()
    return str(value if value.is_absolute() else Path(_BASE_PATH) / value)


ASSET_PATH = data_path("ASSET_PATH")
RESOURCE_PATH = data_path("RESOURCE_PATH")
UPDATE_PATH = data_path("UPDATE_PATH")

# Unity settings
UNITY_SIGNATURE = bytes(config.get("Unity settings", "UNITY_SIGNATURE"), encoding="utf8")
UNITY_VERSION = config.get("Unity settings", "UNITY_VERSION")

# Asset/Resource classify settings
ASSET_CLASSIFY = {
    key: config["Asset classify settings"].get(key) for key in config["Asset classify settings"]
}
RESOURCE_CLASSIFY = {
    key: config["Resource classify settings"].get(key) for key in config["Resource classify settings"]
}
CLASSIFY = {"assetbundle": ASSET_CLASSIFY, "resources": RESOURCE_CLASSIFY}
