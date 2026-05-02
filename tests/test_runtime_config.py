import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.config as config_module
import utils.env_runtime_access as access_module
import utils.runtime_access as runtime_access_module
from server.routers import config as config_router
from utils.runtime_access import ensure_runtime_access_allowed_for_request


class RuntimeConfigTest(unittest.TestCase):
    def _isolated_runtime_paths(self, runtime_path: Path, env_path: Path):
        config_patch = patch.multiple(
            config_module,
            RUNTIME_CONFIG_PATH=runtime_path,
            ENV_PATH=env_path,
        )
        router_patch = patch.multiple(config_router, ENV_PATH=env_path)
        runtime_access_patch = patch.multiple(runtime_access_module, ENV_PATH=env_path)
        return config_patch, router_patch, runtime_access_patch

    def test_runtime_config_masks_secrets_and_updates_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                config_module.save_runtime_config(
                    {
                        "providers": {
                            "openai": {
                                "base_url": "https://llm.example.com/v1",
                                "api_key": "sk-test-openai",
                            },
                            "embedding": {
                                "base_url": "https://embed.example.com/v1",
                                "api_key": "sk-test-embed",
                                "model": "text-embedding-3-large",
                            },
                        },
                        "model_aliases": {
                            "gpt_pro": "openai/gpt-5.4",
                            "gem_flash": "google/gemini-2.5-flash",
                        },
                    }
                )

                view = config_module.load_runtime_config_view()
                self.assertEqual(view["providers"]["openai"]["base_url"], "https://llm.example.com/v1")
                self.assertEqual(view["providers"]["openai"]["api_key"], "")
                self.assertTrue(view["providers"]["openai"]["api_key_configured"])
                self.assertTrue(view["providers"]["openai"]["api_key_masked"])
                self.assertEqual(config_module.resolve_model("gpt_pro"), "gpt_pro")
                self.assertEqual(config_module.resolve_model("missing-alias"), "missing-alias")
                self.assertEqual(config_module.get_embedding_model(), "text-embedding-3-small")

    def test_runtime_override_context_uses_browser_settings_without_env_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        "EMBEDDING_MODEL=text-embedding-3-small",
                        "GPT_PRO=openai/gpt-5.4",
                    ]
                ),
                encoding="utf-8",
            )
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                override_header = "eyJtb2RlbF9hbGlhc2VzIjp7ImdwdF9wcm8iOiJvcGVuYWkvZ3B0LTUtbWluaSJ9LCJwcm92aWRlcnMiOnsiZW1iZWRkaW5nIjp7Im1vZGVsIjoidGV4dC1lbWJlZGRpbmctMy1sYXJnZSJ9fX0"
                override = config_module.parse_runtime_override_header(override_header)

                with config_module.runtime_config_override(override):
                    merged = config_module.load_runtime_config()
                    self.assertEqual(merged["providers"]["openai"]["base_url"], "")
                    self.assertEqual(config_module.get_embedding_model(), "text-embedding-3-large")
                    self.assertEqual(config_module.resolve_model("gpt_pro"), "openai/gpt-5-mini")

    def test_effective_runtime_config_uses_env_without_browser_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        "GPT_PRO=openai/gpt-5.4",
                    ]
                ),
                encoding="utf-8",
            )
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                effective = config_module.load_runtime_config()
                self.assertEqual(effective["providers"]["openai"]["base_url"], "https://shared.example.com/v1")
                self.assertEqual(effective["providers"]["openai"]["api_key"], "shared-openai-key")
                self.assertEqual(config_module.resolve_model("gpt_pro"), "openai/gpt-5.4")

    def test_async_tasks_inherit_runtime_override_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                override = {"model_aliases": {"gpt_pro": "openai/gpt-5-mini"}}

                async def resolve_inside_task() -> str:
                    await asyncio.sleep(0)
                    return config_module.resolve_model("gpt_pro")

                async def run_case() -> str:
                    with config_module.runtime_config_override(override):
                        task = asyncio.create_task(resolve_inside_task())
                    return await task

                resolved = asyncio.run(run_case())
                self.assertEqual(resolved, "openai/gpt-5-mini")

    def test_runtime_mode_context_controls_validation_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=env-key\nENV_RUNTIME_ACCESS_GUARD=off\n",
                encoding="utf-8",
            )
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                self.assertTrue(config_module.has_runtime_config_for_request())
                self.assertEqual(config_module.get_effective_runtime_source(), "env")

                with config_module.runtime_config_override({"providers": {"openai": {"api_key": "browser-key"}}}):
                    self.assertTrue(config_module.has_runtime_config_for_request())
                    self.assertEqual(config_module.get_effective_runtime_source(), "env")

    def test_guard_off_forces_env_even_when_browser_override_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        "GPT_PRO=openai/gpt-5.4",
                        "ENV_RUNTIME_ACCESS_GUARD=off",
                    ]
                ),
                encoding="utf-8",
            )
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                override = {
                    "providers": {
                        "openai": {
                            "base_url": "https://browser.example.com/v1",
                            "api_key": "browser-key",
                        }
                    },
                    "model_aliases": {"gpt_pro": "openai/gpt-5-mini"},
                }

                with config_module.runtime_config_override(override):
                    self.assertFalse(config_module.is_browser_runtime_enabled())
                    self.assertEqual(config_module.get_effective_runtime_source(), "env")
                    effective = config_module.load_runtime_config()
                    self.assertEqual(
                        effective["providers"]["openai"]["base_url"],
                        "https://shared.example.com/v1",
                    )
                    self.assertEqual(
                        effective["providers"]["openai"]["api_key"],
                        "shared-openai-key",
                    )
                    self.assertEqual(
                        config_module.resolve_model("gpt_pro"),
                        "openai/gpt-5.4",
                    )

    def test_guard_off_browser_mode_does_not_use_browser_override_when_env_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text("ENV_RUNTIME_ACCESS_GUARD=off\n", encoding="utf-8")
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                override = {
                    "providers": {"openai": {"api_key": "browser-key"}},
                }

                with config_module.runtime_config_override(override):
                    self.assertIsNone(config_module.get_effective_runtime_source())
                    self.assertFalse(config_module.has_runtime_config_for_request())
                    self.assertIn(
                        "ENV_RUNTIME_ACCESS_GUARD=off",
                        config_module.get_missing_runtime_config_detail(),
                    )
                    self.assertEqual(
                        config_module.load_runtime_config()["providers"]["openai"]["api_key"],
                        "",
                    )

    def test_password_guard_locked_session_falls_back_to_browser_override_without_env_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        "EMBEDDING_MODEL=text-embedding-3-small",
                        "GPT_PRO=openai/gpt-5.4",
                        "ENV_RUNTIME_ACCESS_GUARD=password",
                        f"ENV_RUNTIME_PASSWORD_HASH={access_module.create_env_runtime_password_hash('super-secret-password')}",
                    ]
                ),
                encoding="utf-8",
            )
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                override = {
                    "providers": {
                        "embedding": {"model": "text-embedding-3-large"},
                    },
                    "model_aliases": {"gpt_pro": "openai/gpt-5-mini"},
                }

                with config_module.runtime_config_override(override):
                    self.assertTrue(config_module.is_browser_runtime_enabled())
                    self.assertEqual(
                        config_module.get_effective_runtime_source(),
                        "browser",
                    )
                    merged = config_module.load_runtime_config()
                    self.assertEqual(
                        merged["providers"]["openai"]["base_url"],
                        "",
                    )
                    self.assertEqual(
                        config_module.get_embedding_model(),
                        "text-embedding-3-large",
                    )
                    self.assertEqual(
                        config_module.resolve_model("gpt_pro"),
                        "openai/gpt-5-mini",
                    )

    def test_password_guard_unlocked_env_beats_browser_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            verifier = access_module.create_env_runtime_password_hash("super-secret-password")
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        "GPT_PRO=openai/gpt-5.4",
                        "ENV_RUNTIME_ACCESS_GUARD=password",
                        f"ENV_RUNTIME_PASSWORD_HASH={verifier}",
                    ]
                ),
                encoding="utf-8",
            )
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                override = {
                    "providers": {
                        "openai": {
                            "base_url": "https://browser.example.com/v1",
                            "api_key": "browser-key",
                        }
                    },
                    "model_aliases": {"gpt_pro": "openai/gpt-5-mini"},
                }

                with access_module.env_runtime_auth_override(
                    {"expires_at": time.time() + 3600}
                ):
                    with config_module.runtime_config_override(override):
                        self.assertEqual(config_module.get_effective_runtime_source(), "env")
                        effective = config_module.load_runtime_config()
                        self.assertEqual(
                            effective["providers"]["openai"]["base_url"],
                            "https://shared.example.com/v1",
                        )
                        self.assertEqual(
                            effective["providers"]["openai"]["api_key"],
                            "shared-openai-key",
                        )
                        self.assertEqual(
                            config_module.resolve_model("gpt_pro"),
                            "openai/gpt-5.4",
                        )

    def test_env_runtime_access_guard_off_allows_env_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            verifier = access_module.create_env_runtime_password_hash("super-secret-password")
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=shared-openai-key",
                        f"ENV_RUNTIME_PASSWORD_HASH={verifier}",
                        "ENV_RUNTIME_ACCESS_GUARD=off",
                    ]
                ),
                encoding="utf-8",
            )
            app = FastAPI()
            app.include_router(config_router.router, prefix="/api")
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                ok, detail = ensure_runtime_access_allowed_for_request()
                self.assertTrue(ok)
                self.assertEqual(detail, "")
                self.assertEqual(config_module.load_runtime_config()["providers"]["openai"]["api_key"], "shared-openai-key")

                with TestClient(app) as client:
                    access_response = client.get("/api/config/runtime/access")
                    self.assertEqual(access_response.status_code, 200, access_response.text)
                    self.assertEqual(access_response.json()["env_mode"]["guard_mode"], "off")
                    self.assertFalse(access_response.json()["browser_runtime_enabled"])

                    challenge_response = client.post("/api/config/runtime/env-unlock/challenge")
                    self.assertEqual(challenge_response.status_code, 200, challenge_response.text)
                    payload = challenge_response.json()
                    self.assertFalse(payload["protected"])
                    self.assertEqual(payload["guard_mode"], "off")

    def test_env_runtime_password_guard_unlock_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            verifier = access_module.create_env_runtime_password_hash("super-secret-password")
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        f"ENV_RUNTIME_PASSWORD_HASH={verifier}",
                        "ENV_RUNTIME_ACCESS_GUARD=password",
                    ]
                ),
                encoding="utf-8",
            )
            app = FastAPI()
            app.include_router(config_router.router, prefix="/api")
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                ok, detail = ensure_runtime_access_allowed_for_request()
                self.assertFalse(ok)
                self.assertIn("password-protected", detail)

                self.assertEqual(config_module.load_runtime_config()["providers"]["openai"]["api_key"], "shared-openai-key")

                with TestClient(app) as client:
                    access_response = client.get("/api/config/runtime/access")
                    self.assertEqual(access_response.status_code, 200, access_response.text)
                    self.assertTrue(access_response.json()["browser_runtime_enabled"])

                    challenge_response = client.post("/api/config/runtime/env-unlock/challenge")
                    self.assertEqual(challenge_response.status_code, 200, challenge_response.text)
                    challenge = challenge_response.json()
                    self.assertTrue(challenge["protected"])
                    self.assertEqual(challenge["guard_mode"], "password")

                    salt = access_module._urlsafe_b64decode_bytes(challenge["salt"])
                    derived_key = hashlib.pbkdf2_hmac(
                        "sha256",
                        b"super-secret-password",
                        salt,
                        int(challenge["iterations"]),
                        dklen=32,
                    )
                    payload = (
                        f"{challenge['challenge_id']}:{challenge['nonce']}:{challenge['expires_at']}"
                    ).encode("utf-8")
                    proof = access_module._urlsafe_b64encode_bytes(
                        hmac.new(derived_key, payload, hashlib.sha256).digest()
                    )

                    verify_response = client.post(
                        "/api/config/runtime/env-unlock/verify",
                        json={
                            "challenge_id": challenge["challenge_id"],
                            "proof": proof,
                        },
                    )
                    self.assertEqual(verify_response.status_code, 200, verify_response.text)
                    token = verify_response.json()["token"]

                parsed_auth = access_module.parse_env_runtime_auth_header(
                    token,
                    env_path=env_path,
                    user_agent="testclient",
                )
                self.assertIsNotNone(parsed_auth)

                ok, _ = ensure_runtime_access_allowed_for_request()
                self.assertFalse(ok)
                with access_module.env_runtime_auth_override(parsed_auth):
                    ok, detail = ensure_runtime_access_allowed_for_request()
                    self.assertTrue(ok)
                    self.assertEqual(detail, "")

    def test_password_guard_requires_hash_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=shared-openai-key",
                        "ENV_RUNTIME_ACCESS_GUARD=password",
                    ]
                ),
                encoding="utf-8",
            )
            app = FastAPI()
            app.include_router(config_router.router, prefix="/api")
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                ok, detail = ensure_runtime_access_allowed_for_request()
                self.assertFalse(ok)
                self.assertIn("ENV_RUNTIME_PASSWORD_HASH", detail)

                with TestClient(app) as client:
                    challenge_response = client.post("/api/config/runtime/env-unlock/challenge")
                    self.assertEqual(challenge_response.status_code, 503, challenge_response.text)

    def test_env_runtime_security_routes_do_not_leak_plaintext_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            runtime_path = temp_root / "runtime_config.yaml"
            env_path = temp_root / ".env"
            plaintext_password = "super-secret-password"
            verifier = access_module.create_env_runtime_password_hash(plaintext_password)
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_BASE_URL=https://shared.example.com/v1",
                        "OPENAI_API_KEY=shared-openai-key",
                        f"ENV_RUNTIME_PASSWORD_HASH={verifier}",
                        "ENV_RUNTIME_ACCESS_GUARD=password",
                    ]
                ),
                encoding="utf-8",
            )
            app = FastAPI()
            app.include_router(config_router.router, prefix="/api")
            config_patch, router_patch, runtime_access_patch = self._isolated_runtime_paths(runtime_path, env_path)
            with config_patch, router_patch, runtime_access_patch, patch.dict(os.environ, {}, clear=True):
                config_module._invalidate_runtime_config_cache()
                self.assertNotIn(plaintext_password, verifier)

                with TestClient(app) as client:
                    runtime_response = client.get("/api/config/runtime")
                    self.assertEqual(runtime_response.status_code, 200, runtime_response.text)
                    runtime_text = runtime_response.text
                    self.assertNotIn(plaintext_password, runtime_text)
                    self.assertNotIn("shared-openai-key", runtime_text)
                    self.assertNotIn("ENV_RUNTIME_PASSWORD_HASH", runtime_text)

                    access_response = client.get("/api/config/runtime/access")
                    self.assertEqual(access_response.status_code, 200, access_response.text)
                    self.assertNotIn(plaintext_password, access_response.text)
                    self.assertNotIn(verifier, access_response.text)

                    challenge_response = client.post("/api/config/runtime/env-unlock/challenge")
                    self.assertEqual(challenge_response.status_code, 200, challenge_response.text)
                    challenge_text = challenge_response.text
                    self.assertNotIn(plaintext_password, challenge_text)
                    self.assertNotIn(verifier, challenge_text)
                    self.assertNotIn("derived_key", challenge_text)

    def test_generate_env_password_hash_script_rejects_plaintext_cli_argument(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "generate_env_runtime_password_hash.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--password",
                "plain-text-secret",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
