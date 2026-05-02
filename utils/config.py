import copy
import json
import os
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from utils.env_runtime_access import (
    ENV_RUNTIME_ACCESS_GUARD_PASSWORD,
    get_env_runtime_access_guard_mode,
    is_env_runtime_password_configured,
    is_env_runtime_access_allowed,
)
from utils.repo_paths import PROJECT_ROOT, normalize_config_paths

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
ENV_PATH = PROJECT_ROOT / ".env"
RUNTIME_CONFIG_PATH = DATA_DIR / "runtime_config.yaml"
RESULTS_DIR = PROJECT_ROOT / "results"
ASSETS_DIR = RESULTS_DIR / "assets"
LOCAL_PDF_DIR = DATA_DIR / "local"
FETCH_PDF_DIR = DATA_DIR / "fetch"
CACHE_DIR = DATA_DIR / "cache"
RUNTIME_OVERRIDE_HEADER = "x-paper-agent-runtime"


def _parse_proxy_port(raw_value: str | None) -> int | None:
    value = (raw_value or "").strip()
    if not value or value.lower() in {"none", "null"}:
        return None
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("PROXY_PORT 必须是端口号，或 None / Null") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PROXY_PORT 必须在 1 到 65535 之间")
    return port


DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "providers": {
        "openai": {
            "base_url": "",
            "api_key": "",
        },
        "lite": {
            "base_url": "",
            "api_key": "",
        },
        "embedding": {
            "base_url": "",
            "api_key": "",
            "model": "text-embedding-3-small",
        },
        "semantic_scholar": {
            "api_key": "",
        },
        "mineru": {
            "api_key": "",
        },
        "r2": {
            "endpoint": "",
            "bucket": "",
            "access_key_id": "",
            "secret_access_key": "",
            "public_base_url": "",
        },
        "network": {
            "proxy_port": None,
        },
    },
    "model_aliases": {
        "gpt_pro": "",
        "gem_pro": "",
        "gem_flash": "",
        "lite_model": "",
        "gem_image": "",
    },
}

_SECRET_RUNTIME_FIELDS = {
    "providers.openai.api_key",
    "providers.lite.api_key",
    "providers.embedding.api_key",
    "providers.semantic_scholar.api_key",
    "providers.mineru.api_key",
    "providers.r2.access_key_id",
    "providers.r2.secret_access_key",
}

_runtime_config_cache: dict[str, Any] | None = None
_runtime_config_cache_mtime_ns: int | None = None
_runtime_config_override_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "paper_agent_runtime_override",
    default=None,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "topics": [],
    "selection": {
        "track": "auto",
        "candidate_pool_size": 80,
        "date_range_days": 7,
        "classic_min_citations": 50,
        "semantic_top_k": 8,
        "min_semantic_score": 0.4,
        "topic_fit_gate_threshold": 0.72,
        "post_download_topic_fit_threshold": 0.55,
        "preferred_venues": [],
        "preferred_institutions": [],
    },
    "models": {
        "fast": "gem_flash",
        "primary": "gem_pro",
        "secondary": "gpt_pro",
        "merge_model": "gem_pro",
        "reasoning_effort": "high",
    },
    "report": {
        "structure_mode": "classic",
    },
    "output": {
        "results_dir": "results",
        "assets_dir": "results/assets",
        "filename_pattern": "{title_short}",
    },
    "storage": {
        "local_dir": "data/local",
        "fetch_dir": "data/fetch",
        "cache_dir": "data/cache",
        "keep_cache": False,
    },
}


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} 顶层必须是对象")
    return payload


def _normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    providers = config.setdefault("providers", {})
    openai_provider = providers.setdefault("openai", {})
    lite_provider = providers.setdefault("lite", {})
    embedding_provider = providers.setdefault("embedding", {})
    semantic_provider = providers.setdefault("semantic_scholar", {})
    mineru_provider = providers.setdefault("mineru", {})
    r2_provider = providers.setdefault("r2", {})
    network_provider = providers.setdefault("network", {})

    for provider in (
        openai_provider,
        lite_provider,
        embedding_provider,
        semantic_provider,
        mineru_provider,
        r2_provider,
    ):
        for key, value in list(provider.items()):
            if value is None:
                provider[key] = ""
            elif isinstance(value, str):
                provider[key] = value.strip()

    model_aliases = config.setdefault("model_aliases", {})
    for key, value in list(model_aliases.items()):
        model_aliases[key] = str(value or "").strip()

    proxy_port = network_provider.get("proxy_port")
    if proxy_port in {"", None, "none", "null", "None", "Null"}:
        network_provider["proxy_port"] = None
    else:
        try:
            network_provider["proxy_port"] = _parse_proxy_port(str(proxy_port))
        except ValueError:
            network_provider["proxy_port"] = None

    embedding_provider["model"] = str(
        embedding_provider.get("model") or "text-embedding-3-small"
    ).strip() or "text-embedding-3-small"
    return config


def _invalidate_runtime_config_cache() -> None:
    global _runtime_config_cache, _runtime_config_cache_mtime_ns
    _runtime_config_cache = None
    _runtime_config_cache_mtime_ns = None


def _load_runtime_config_base(config_path: Path | None = None) -> dict[str, Any]:
    ensure_data_dirs()
    path = config_path or RUNTIME_CONFIG_PATH
    if config_path is None:
        global _runtime_config_cache, _runtime_config_cache_mtime_ns
        current_mtime_ns = path.stat().st_mtime_ns if path.exists() else -1
        if (
            _runtime_config_cache is not None
            and _runtime_config_cache_mtime_ns == current_mtime_ns
        ):
            return copy.deepcopy(_runtime_config_cache)

    raw = _read_yaml_dict(path)
    config = _normalize_runtime_config(
        _deep_merge(copy.deepcopy(DEFAULT_RUNTIME_CONFIG), raw)
    )

    if config_path is None:
        _runtime_config_cache = copy.deepcopy(config)
        _runtime_config_cache_mtime_ns = path.stat().st_mtime_ns if path.exists() else -1
    return copy.deepcopy(config)


def get_runtime_override_header_name() -> str:
    return RUNTIME_OVERRIDE_HEADER


def get_active_runtime_override() -> dict[str, Any] | None:
    override = _runtime_config_override_var.get()
    if not isinstance(override, dict) or not override:
        return None
    return copy.deepcopy(override)


def has_active_runtime_override() -> bool:
    return get_active_runtime_override() is not None


def get_requested_runtime_source() -> str | None:
    return get_effective_runtime_source()


def is_browser_runtime_enabled(*, env_path: Path | None = None) -> bool:
    return (
        get_env_runtime_access_guard_mode(env_path=env_path or ENV_PATH)
        == ENV_RUNTIME_ACCESS_GUARD_PASSWORD
    )


def get_effective_runtime_source() -> str | None:
    browser_enabled = is_browser_runtime_enabled()
    env_available = has_env_runtime_config()
    override = get_active_runtime_override()
    env_access_allowed = is_env_runtime_access_allowed(env_path=ENV_PATH)

    if not browser_enabled:
        return "env" if env_available else None

    if env_access_allowed:
        return "env" if env_available else None

    if override:
        return "browser"
    return None


def sanitize_runtime_override(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return _sanitize_runtime_update(payload)


def parse_runtime_override_header(raw_header: str | None) -> dict[str, Any] | None:
    raw_value = str(raw_header or "").strip()
    if not raw_value:
        return None
    padding = "=" * (-len(raw_value) % 4)
    try:
        decoded = urlsafe_b64decode(f"{raw_value}{padding}".encode("ascii")).decode(
            "utf-8"
        )
    except Exception as exc:
        raise ValueError("Invalid runtime override header encoding") from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid runtime override header payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("Runtime override header must decode to a JSON object")
    sanitized = sanitize_runtime_override(payload)
    return sanitized or None


@contextmanager
def runtime_config_override(payload: dict[str, Any] | None):
    sanitized = sanitize_runtime_override(payload)
    token = _runtime_config_override_var.set(sanitized or None)
    try:
        yield
    finally:
        _runtime_config_override_var.reset(token)


def _first_env_value(env_values: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(env_values.get(name) or "").strip()
        if value:
            return value
    return ""


def _load_relevant_env_values(env_path: Path | None = None) -> dict[str, str]:
    path = env_path or ENV_PATH
    values: dict[str, str] = {}
    if path.exists():
        loaded = dotenv_values(path)
        for key, value in loaded.items():
            if key and value is not None:
                values[str(key)] = str(value)

    relevant_names = {
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "LITE_BASE_URL",
        "LITE_API_KEY",
        "LITE_MODEL",
        "LITE_MODLE",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "SEMANTIC_SCHOLAR_API_KEY",
        "SEMANTIC_SCHOLAR_KEY",
        "S2_API_KEY",
        "MINERU_KEY",
        "MINERU_API_KEY",
        "R2_ENDPOINT",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_PUBLIC_BASE_URL",
        "PROXY_PORT",
        "GPT_PRO",
        "GEM_PRO",
        "GEM_FLASH",
        "GEM_IMAGE",
    }
    for key in relevant_names:
        value = os.environ.get(key)
        if value is not None:
            values[key] = value
    return values


def load_env_runtime_config(env_path: Path | None = None) -> dict[str, Any]:
    env_values = _load_relevant_env_values(env_path)
    proxy_port = _first_env_value(env_values, "PROXY_PORT")
    runtime = copy.deepcopy(DEFAULT_RUNTIME_CONFIG)
    runtime["providers"]["openai"]["base_url"] = _first_env_value(
        env_values, "OPENAI_BASE_URL"
    )
    runtime["providers"]["openai"]["api_key"] = _first_env_value(
        env_values, "OPENAI_API_KEY"
    )
    runtime["providers"]["lite"]["base_url"] = _first_env_value(
        env_values, "LITE_BASE_URL"
    )
    runtime["providers"]["lite"]["api_key"] = _first_env_value(
        env_values, "LITE_API_KEY"
    )
    runtime["providers"]["embedding"]["base_url"] = _first_env_value(
        env_values, "EMBEDDING_BASE_URL"
    )
    runtime["providers"]["embedding"]["api_key"] = _first_env_value(
        env_values, "EMBEDDING_API_KEY"
    )
    runtime["providers"]["embedding"]["model"] = (
        _first_env_value(env_values, "EMBEDDING_MODEL")
        or runtime["providers"]["embedding"]["model"]
    )
    runtime["providers"]["semantic_scholar"]["api_key"] = _first_env_value(
        env_values,
        "SEMANTIC_SCHOLAR_API_KEY",
        "SEMANTIC_SCHOLAR_KEY",
        "S2_API_KEY",
    )
    runtime["providers"]["mineru"]["api_key"] = _first_env_value(
        env_values,
        "MINERU_API_KEY",
        "MINERU_KEY",
    )
    runtime["providers"]["r2"]["endpoint"] = _first_env_value(
        env_values, "R2_ENDPOINT"
    )
    runtime["providers"]["r2"]["bucket"] = _first_env_value(env_values, "R2_BUCKET")
    runtime["providers"]["r2"]["access_key_id"] = _first_env_value(
        env_values, "R2_ACCESS_KEY_ID"
    )
    runtime["providers"]["r2"]["secret_access_key"] = _first_env_value(
        env_values, "R2_SECRET_ACCESS_KEY"
    )
    runtime["providers"]["r2"]["public_base_url"] = _first_env_value(
        env_values, "R2_PUBLIC_BASE_URL"
    )
    if proxy_port:
        runtime["providers"]["network"]["proxy_port"] = _parse_proxy_port(proxy_port)
    runtime["model_aliases"]["gpt_pro"] = _first_env_value(env_values, "GPT_PRO")
    runtime["model_aliases"]["gem_pro"] = _first_env_value(env_values, "GEM_PRO")
    runtime["model_aliases"]["gem_flash"] = _first_env_value(
        env_values, "GEM_FLASH"
    )
    runtime["model_aliases"]["gem_image"] = _first_env_value(
        env_values, "GEM_IMAGE"
    )
    runtime["model_aliases"]["lite_model"] = _first_env_value(
        env_values, "LITE_MODEL", "LITE_MODLE"
    )
    return _normalize_runtime_config(runtime)


def _runtime_config_has_values(config: dict[str, Any]) -> bool:
    providers = config.get("providers", {}) if isinstance(config, dict) else {}
    for provider_payload in providers.values():
        if not isinstance(provider_payload, dict):
            continue
        for value in provider_payload.values():
            if value is None:
                continue
            if isinstance(value, str) and value.strip():
                return True
            if not isinstance(value, str) and value != "":
                return True

    aliases = config.get("model_aliases", {}) if isinstance(config, dict) else {}
    return any(str(value or "").strip() for value in aliases.values())


def has_env_runtime_config() -> bool:
    return _runtime_config_has_values(load_env_runtime_config())


def has_runtime_config_for_request() -> bool:
    return get_effective_runtime_source() is not None


def get_missing_runtime_config_detail() -> str:
    browser_enabled = is_browser_runtime_enabled()
    env_access_allowed = is_env_runtime_access_allowed(env_path=ENV_PATH)
    env_password_configured = is_env_runtime_password_configured(env_path=ENV_PATH)

    if not browser_enabled:
        return (
            "ENV_RUNTIME_ACCESS_GUARD=off, so the backend only uses the server .env runtime settings. "
            "The server .env does not provide usable runtime settings."
        )

    if env_access_allowed:
        return (
            "Server .env access is currently unlocked, so the backend is trying to use the server .env runtime settings. "
            "The server .env does not provide usable runtime settings. Fill in .env or lock env access again to fall back to browser-local settings."
        )

    if not env_password_configured:
        return (
            "Env runtime access guard is enabled, but ENV_RUNTIME_PASSWORD_HASH is missing on the server. "
            "Fill in /settings for this browser, or fix the server env guard configuration."
        )

    return (
        "Server .env access is locked, so the backend is waiting for this browser's runtime settings. "
        "Fill in /settings for this browser or unlock the server .env access."
    )


def load_runtime_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is not None:
        return _load_runtime_config_base(config_path=config_path)

    effective_source = get_effective_runtime_source()
    if effective_source == "browser":
        override = get_active_runtime_override()
        return _normalize_runtime_config(
            _deep_merge(copy.deepcopy(DEFAULT_RUNTIME_CONFIG), override or {})
        )

    if effective_source == "env":
        return load_env_runtime_config()

    return _normalize_runtime_config(copy.deepcopy(DEFAULT_RUNTIME_CONFIG))


def _mask_secret(value: str) -> str:
    secret = str(value or "").strip()
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * max(len(secret) - 8, 4)}{secret[-4:]}"


def load_runtime_config_view() -> dict[str, Any]:
    config = _load_runtime_config_base()
    secret_status: dict[str, dict[str, Any]] = {}
    for field_path in _SECRET_RUNTIME_FIELDS:
        target = config
        keys = field_path.split(".")
        for key in keys[:-1]:
            target = target.get(key, {}) if isinstance(target, dict) else {}
        field_name = keys[-1]
        raw_value = (
            str(target.get(field_name) or "").strip()
            if isinstance(target, dict)
            else ""
        )
        secret_status[field_path] = {
            "configured": bool(raw_value),
            "masked_value": _mask_secret(raw_value),
        }
        if isinstance(target, dict):
            target[field_name] = ""
            target[f"{field_name}_configured"] = bool(raw_value)
            target[f"{field_name}_masked"] = _mask_secret(raw_value)
    config["secret_status"] = secret_status
    return config


def _set_nested_value(target: dict[str, Any], path: str, value: Any) -> None:
    keys = [segment for segment in path.split(".") if segment]
    if not keys:
        return
    cursor = target
    for key in keys[:-1]:
        existing = cursor.get(key)
        if not isinstance(existing, dict):
            existing = {}
            cursor[key] = existing
        cursor = existing
    cursor[keys[-1]] = value


def _sanitize_runtime_update(payload: dict[str, Any]) -> dict[str, Any]:
    providers_payload = payload.get("providers", {})
    aliases_payload = payload.get("model_aliases", {})
    sanitized: dict[str, Any] = {"providers": {}, "model_aliases": {}}

    if isinstance(providers_payload, dict):
        default_providers = DEFAULT_RUNTIME_CONFIG.get("providers", {})
        for provider_name, default_provider in default_providers.items():
            provider_updates = providers_payload.get(provider_name, {})
            if not isinstance(default_provider, dict) or not isinstance(
                provider_updates, dict
            ):
                continue
            sanitized_provider: dict[str, Any] = {}
            for field_name in default_provider.keys():
                if field_name not in provider_updates:
                    continue
                if field_name.endswith("_configured") or field_name.endswith("_masked"):
                    continue
                raw_value = provider_updates.get(field_name)
                field_path = f"providers.{provider_name}.{field_name}"
                if field_path in _SECRET_RUNTIME_FIELDS:
                    if raw_value is None:
                        continue
                    text_value = str(raw_value).strip()
                    if not text_value:
                        continue
                    sanitized_provider[field_name] = text_value
                    continue
                if provider_name == "network" and field_name == "proxy_port":
                    if raw_value in {"", None, "none", "null", "None", "Null"}:
                        sanitized_provider[field_name] = None
                    else:
                        sanitized_provider[field_name] = _parse_proxy_port(
                            str(raw_value)
                        )
                    continue
                sanitized_provider[field_name] = (
                    str(raw_value).strip() if raw_value is not None else ""
                )
            if sanitized_provider:
                sanitized["providers"][provider_name] = sanitized_provider

    if isinstance(aliases_payload, dict):
        default_aliases = DEFAULT_RUNTIME_CONFIG.get("model_aliases", {})
        for alias_name in default_aliases.keys():
            if alias_name not in aliases_payload:
                continue
            sanitized["model_aliases"][alias_name] = str(
                aliases_payload.get(alias_name) or ""
            ).strip()

    return sanitized


def save_runtime_config(
    updates: dict[str, Any],
    *,
    clear_secrets: list[str] | None = None,
    config_path: Path | None = None,
) -> None:
    ensure_data_dirs()
    path = config_path or RUNTIME_CONFIG_PATH
    existing = _read_yaml_dict(path)
    filtered_updates = _sanitize_runtime_update(updates)
    merged = _deep_merge(existing, filtered_updates)

    for field_path in clear_secrets or []:
        if field_path not in _SECRET_RUNTIME_FIELDS:
            continue
        _set_nested_value(merged, field_path, "")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            merged,
            fh,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    _invalidate_runtime_config_cache()


def get_proxy_port_value() -> int | None:
    network = load_runtime_config().get("providers", {}).get("network", {})
    raw_proxy_port = network.get("proxy_port")
    if raw_proxy_port in {None, ""}:
        return None
    try:
        return _parse_proxy_port(str(raw_proxy_port))
    except ValueError:
        return None


def get_proxy_url() -> str | None:
    proxy_port = get_proxy_port_value()
    return f"http://127.0.0.1:{proxy_port}" if proxy_port is not None else None


def get_httpx_client_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"trust_env": False}
    proxy_url = get_proxy_url()
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return kwargs


def get_botocore_proxies() -> dict[str, str]:
    proxy_url = get_proxy_url()
    if not proxy_url:
        return {}
    return {"http": proxy_url, "https": proxy_url}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def ensure_data_dirs() -> None:
    for path in (RESULTS_DIR, ASSETS_DIR, DATA_DIR, LOCAL_PDF_DIR, FETCH_PDF_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def clear_cache_dir(cache_dir: Path) -> None:
    if not cache_dir.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        return
    for child in cache_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    ensure_data_dirs()
    path = config_path or CONFIG_PATH
    if not path.exists():
        return dict(DEFAULT_CONFIG)

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ValueError("config.yaml 顶层必须是对象")

    config = _deep_merge(DEFAULT_CONFIG, raw)
    storage = config.setdefault("storage", {})
    storage["keep_cache"] = bool(storage.get("keep_cache", False))
    return normalize_config_paths(config)


def resolve_model(alias: str) -> str:
    model_aliases = load_runtime_config().get("model_aliases", {})
    resolved = str(model_aliases.get(alias, "") or "").strip()
    return resolved or alias


def get_embedding_model() -> str:
    providers = load_runtime_config().get("providers", {})
    embedding = providers.get("embedding", {})
    return str(embedding.get("model") or "text-embedding-3-small").strip() or "text-embedding-3-small"
