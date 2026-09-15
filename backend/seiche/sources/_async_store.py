"""Bound synchronous source storage without occupying the API health pool."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
from typing import Callable, ParamSpec, TypeVar


_PARAMS = ParamSpec("_PARAMS")
_RESULT = TypeVar("_RESULT")
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="seiche-source-store")


async def run_store(
    operation: Callable[_PARAMS, _RESULT],
    *args: _PARAMS.args,
    **kwargs: _PARAMS.kwargs,
) -> _RESULT:
    """Keep disk I/O and store locks off the serving loop and default executor.

    Source fan-out can queue many cache reads behind the SQLite lock. A
    separate bounded pool leaves health and control checks able to progress.
    Like asyncio.to_thread, cancellation cannot interrupt an active write;
    the existing store transaction remains responsible for atomicity.
    """
    context = copy_context()
    call = partial(context.run, operation, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_EXECUTOR, call)
