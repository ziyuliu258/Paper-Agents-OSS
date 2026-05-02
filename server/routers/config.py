"""Config read/write API."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request

from server.schemas import (
    ConfigSaveResponse,
    EnvRuntimeUnlockChallengeResponse,
    EnvRuntimeUnlockVerifyRequest,
    EnvRuntimeUnlockVerifyResponse,
    RuntimeAccessResponse,
    RuntimeConfigResponse,
    RuntimeConfigUpdate,
)
from utils.config import (
    CONFIG_PATH,
    ENV_PATH,
    is_browser_runtime_enabled,
    load_config,
    load_runtime_config_view,
    save_runtime_config,
)
from utils.env_runtime_access import (
    create_env_runtime_unlock_challenge,
    get_env_runtime_access_snapshot,
    verify_env_runtime_unlock_proof,
)

router = APIRouter(tags=["config"])


@router.get("/config")
async def get_config() -> dict[str, Any]:
    return load_config()


@router.put("/config", response_model=ConfigSaveResponse)
async def update_config(body: dict[str, Any]) -> dict[str, str]:
    # Only write user-facing keys, not resolved storage paths
    writable_keys = {"topics", "selection", "models", "report", "output", "storage"}
    filtered = {k: v for k, v in body.items() if k in writable_keys}

    # Read existing, merge, write back
    existing = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    existing = {k: v for k, v in existing.items() if k in writable_keys}
    existing.update(filtered)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return {"status": "ok"}


@router.get("/config/runtime", response_model=RuntimeConfigResponse)
async def get_runtime_config() -> dict[str, Any]:
    return load_runtime_config_view()


@router.get("/config/runtime/access", response_model=RuntimeAccessResponse)
async def get_runtime_access() -> dict[str, Any]:
    return {
        "browser_runtime_enabled": is_browser_runtime_enabled(env_path=ENV_PATH),
        "env_mode": get_env_runtime_access_snapshot(env_path=ENV_PATH),
    }


@router.put("/config/runtime", response_model=ConfigSaveResponse)
async def update_runtime_config(body: RuntimeConfigUpdate) -> dict[str, str]:
    save_runtime_config(
        {
            "providers": dict(body.providers or {}),
            "model_aliases": dict(body.model_aliases or {}),
        },
        clear_secrets=list(body.clear_secrets or []),
    )
    return {"status": "ok"}


@router.post(
    "/config/runtime/env-unlock/challenge",
    response_model=EnvRuntimeUnlockChallengeResponse,
)
async def create_env_unlock_challenge() -> dict[str, Any]:
    try:
        return create_env_runtime_unlock_challenge(env_path=ENV_PATH)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/config/runtime/env-unlock/verify",
    response_model=EnvRuntimeUnlockVerifyResponse,
)
async def verify_env_unlock(
    body: EnvRuntimeUnlockVerifyRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return verify_env_runtime_unlock_proof(
            env_path=ENV_PATH,
            challenge_id=body.challenge_id,
            proof=body.proof,
            user_agent=request.headers.get("user-agent", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
