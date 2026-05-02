from __future__ import annotations

from typing import Any

from utils.config import (
    ENV_PATH,
    get_effective_runtime_source,
    get_missing_runtime_config_detail,
    has_runtime_config_for_request,
)
from utils.env_runtime_access import (
    get_env_runtime_access_denied_detail,
    is_env_runtime_access_allowed,
)


def get_runtime_access_status_for_request() -> dict[str, Any]:
    source = get_effective_runtime_source()
    runtime_available = has_runtime_config_for_request()
    access_allowed = True
    denied_detail = ""

    if source == "env":
        access_allowed = is_env_runtime_access_allowed(env_path=ENV_PATH)
        if not access_allowed:
            denied_detail = get_env_runtime_access_denied_detail(env_path=ENV_PATH)

    return {
        "source": source,
        "runtime_available": runtime_available,
        "access_allowed": access_allowed,
        "missing_detail": "" if runtime_available else get_missing_runtime_config_detail(),
        "access_denied_detail": denied_detail,
    }


def ensure_runtime_access_allowed_for_request() -> tuple[bool, str]:
    status = get_runtime_access_status_for_request()
    if not status["runtime_available"]:
        return False, str(status["missing_detail"] or "")
    if not status["access_allowed"]:
        return False, str(status["access_denied_detail"] or "")
    return True, ""
