import os
import json
import urllib.request
import urllib.error
import win32crypt
from datetime import datetime

# Centralized Logging for this module
def _ts_log(level: str, msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{level}] {msg}")

def log_info(msg: str): _ts_log("info", msg)
def log_warn(msg: str): _ts_log("warn", msg)
def log_error(msg: str): _ts_log("error", msg)

# --- Remote Configuration Parameters ---
# These parameters represent the single source of truth for the Laravel connection
# API_URL = os.environ.get("TAGPILOT_API_URL", "https://hrmx.hudsmersolutions.com/api/tagpilot/config")
API_URL = os.environ.get("TAGPILOT_API_URL", "http://127.0.0.1:8000/api/tagpilot/config")

# NOTE: The EXE contains this authentication secret. This acts as an access-control 
# mechanism rather than an unextractable secret. Keep development and production 
# secrets separate by overriding this via the TAGPILOT_API_SECRET environment variable.
API_SECRET = os.environ.get("TAGPILOT_API_SECRET", "Hudsmer@tagpilot")

DPAPI_DESCRIPTION = "TagPilot Remote Config Cache"

class RemoteConfigProvider:
    @staticmethod
    def get_cache_path():
        app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'TagPilot')
        os.makedirs(app_data_dir, exist_ok=True)
        return os.path.join(app_data_dir, "ai_config.dat")

    @staticmethod
    def save_cache(config_data: dict):
        try:
            json_str = json.dumps(config_data)
            encrypted = win32crypt.CryptProtectData(
                json_str.encode('utf-8'),
                DPAPI_DESCRIPTION,
                None,
                None,
                None,
                0
            )
            with open(RemoteConfigProvider.get_cache_path(), "wb") as f:
                f.write(encrypted)
        except Exception as e:
            log_error(f"Failed to save remote configuration cache safely")

    @staticmethod
    def load_cache() -> dict:
        cache_path = RemoteConfigProvider.get_cache_path()
        if not os.path.exists(cache_path):
            return None
            
        try:
            with open(cache_path, "rb") as f:
                encrypted = f.read()
                
            _, decrypted = win32crypt.CryptUnprotectData(
                encrypted,
                None,
                None,
                None,
                0
            )
            return json.loads(decrypted.decode('utf-8'))
        except Exception as e:
            log_warn(f"Failed to load remote configuration cache safely")
            return None

    @staticmethod
    def fetch_remote_config() -> dict:
        log_info(f"Attempting to fetch remote config from URL: {API_URL}")
        req = urllib.request.Request(API_URL)
        req.add_header("X-TagPilot-Auth-Key", API_SECRET)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "TagPilot/1.0 (Windows NT 10.0; Win64; x64)")
        
        try:
            # Create an opener that explicitly bypasses the system proxy.
            # (In many corporate/Windows environments, urllib will try to route 127.0.0.1 
            # through a proxy that drops the connection, causing a timeout)
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            
            with opener.open(req, timeout=10) as response:
                log_info(f"API Connection established. Status: {response.status}")
                if response.status == 200:
                    raw_data = response.read().decode('utf-8')
                    # (the body contains API keys: never write it to the log)
                    data = json.loads(raw_data)
                    if data.get("success") is True and isinstance(data.get("credentials"), list) and isinstance(data.get("models"), list):
                        return data
                    else:
                        log_error(f"Remote configuration invalid structure. Keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
                else:
                    log_warn(f"Remote configuration failed with status: {response.status}")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')
            log_warn(f"Remote configuration HTTP error: {e.code} {e.reason}. Response: {error_body}")
        except urllib.error.URLError as e:
            log_warn(f"Remote configuration URL error: server unreachable. Reason: {e.reason}")
        except json.JSONDecodeError as e:
            log_error(f"Remote configuration returned invalid JSON: {e}")
        except Exception as e:
            log_error(f"Remote configuration unexpected error: {e}")
        return None

    @staticmethod
    def get_config() -> dict:
        remote_data = RemoteConfigProvider.fetch_remote_config()
        if remote_data:
            cred_count = len(remote_data.get("credentials", []))
            model_count = len(remote_data.get("models", []))
            log_info(f"Remote configuration synchronized. Credential count: {cred_count}, Model count: {model_count}. Configuration updated: yes")
            RemoteConfigProvider.save_cache(remote_data)
            return remote_data
            
        log_warn("Falling back to local configuration cache")
        cached_data = RemoteConfigProvider.load_cache()
        if cached_data:
            cred_count = len(cached_data.get("credentials", []))
            model_count = len(cached_data.get("models", []))
            log_info(f"Loaded from cache. Credential count: {cred_count}, Model count: {model_count}")
            return cached_data
            
        log_error("No remote configuration available and no valid cache found")
        return None
