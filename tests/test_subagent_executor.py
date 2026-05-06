"""测试 SubagentExecutor 同步包装"""

import pytest
from unittest.mock import MagicMock, AsyncMock


def test_executor_has_dispatch_sync():
    """SubagentExecutor 应有 dispatch_sync 方法"""
    from subagent.executor import SubagentExecutor
    from subagent.deferred_store import DeferredResultStore

    store = MagicMock(spec=DeferredResultStore)
    llm_call = AsyncMock(return_value="result")
    executor = SubagentExecutor(deferred_store=store, llm_call=llm_call)

    assert hasattr(executor, "dispatch_sync")
    assert callable(executor.dispatch_sync)
