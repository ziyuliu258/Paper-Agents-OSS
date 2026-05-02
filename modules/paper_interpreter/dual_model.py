"""Bounded dual-model orchestration for structured section generation."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from utils.llm import call_llm
from utils.logger import get_logger

log = get_logger(__name__)

_DEFAULT_DUAL_STEP_TIMEOUT = 420.0
_DEFAULT_SECOND_MODEL_GRACE = 120.0


async def _run_model_call(
    model_alias: str,
    messages: list[dict[str, Any]],
    *,
    deadline: float | None,
    temperature: float,
    max_tokens: int,
) -> str:
    return await call_llm(
        model_alias,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        deadline=deadline,
    )


async def collect_dual_model_responses(
    messages: list[dict[str, Any]],
    context_label: str,
    *,
    model_a: str = "gem_pro",
    model_b: str = "gpt_pro",
    step_timeout: float | None = _DEFAULT_DUAL_STEP_TIMEOUT,
    second_model_grace_period: float | None = _DEFAULT_SECOND_MODEL_GRACE,
    temperature: float = 0.2,
    max_tokens: int = 8192,
) -> tuple[list[tuple[str, str]], list[tuple[str, Exception]]]:
    """Collect up to two model responses within a bounded step budget."""
    deadline = None if step_timeout is None else time.monotonic() + step_timeout
    grace_deadline: float | None = None

    tasks = {
        asyncio.create_task(
            _run_model_call(
                model_a,
                messages,
                deadline=deadline,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        ): model_a,
        asyncio.create_task(
            _run_model_call(
                model_b,
                messages,
                deadline=deadline,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        ): model_b,
    }

    pending = set(tasks)
    successes: list[tuple[str, str]] = []
    failures: list[tuple[str, Exception]] = []

    while pending:
        now = time.monotonic()
        active_deadline = deadline
        if grace_deadline is not None:
            active_deadline = (
                grace_deadline if deadline is None else min(deadline, grace_deadline)
            )

        timeout: float | None = None
        if active_deadline is not None:
            timeout = max(0.0, active_deadline - now)
            if timeout <= 0:
                break

        done, pending = await asyncio.wait(
            pending,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            break

        for task in done:
            model_alias = tasks[task]
            try:
                result = task.result()
            except Exception as err:
                failures.append((model_alias, err))
                log.warning(
                    "%s: %s 在当前步骤失败，已跳过。原因：%s",
                    context_label,
                    model_alias,
                    err,
                )
            else:
                successes.append((model_alias, result))
                if len(successes) == 1 and second_model_grace_period is not None:
                    candidate = time.monotonic() + second_model_grace_period
                    grace_deadline = (
                        candidate if deadline is None else min(deadline, candidate)
                    )

        if len(successes) == 2:
            break

    if pending:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in pending:
            model_alias = tasks[task]
            failures.append(
                (model_alias, TimeoutError(f"{model_alias} 未在预算内完成"))
            )
            log.warning("%s: %s 未在预算内完成，已取消。", context_label, model_alias)

    return successes, failures


def pick_preferred_response(
    successes: list[tuple[str, Any]],
    *,
    preferred_order: tuple[str, ...] = ("gpt_pro", "gem_pro"),
) -> tuple[str, Any]:
    if not successes:
        raise ValueError("No successful responses available")

    order = {model_alias: idx for idx, model_alias in enumerate(preferred_order)}
    return min(
        successes,
        key=lambda item: (order.get(item[0], len(order)), -len(str(item[1]))),
    )
