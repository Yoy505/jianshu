"""Celery 真实集成：跨进程 worker + 真实 Redis broker/result 的任务提交、结果与失败态。

不使用 always_eager/mock；worker 为子进程启动的独立 celery 进程（见 conftest）。
选用安全任务：send_sms（test 环境仅日志，不触达外部）；失败态用错误参数触发。
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("JIANSHU_PG_TEST_URL") and os.environ.get("JIANSHU_REDIS_TEST_URL")),
    reason="需要 JIANSHU_PG_TEST_URL 与 JIANSHU_REDIS_TEST_URL（真实 broker + worker）",
)


def test_celery_task_executes_across_processes(celery_worker):
    from app.workers.tasks import send_sms

    result = send_sms.delay("13800000001", "246810")
    value = result.get(timeout=30)
    assert value == {"phone": "13800000001", "sent": True}
    assert result.successful() is True
    # 幂等重复提交（同一任务再次调用）仍可执行并返回结果
    result2 = send_sms.delay("13800000001", "246810")
    assert result2.get(timeout=30)["sent"] is True


def test_celery_failure_state_visible(celery_worker):
    """错误参数产生 FAILURE 状态且可读取异常类型（受控恢复依据）。"""
    # send_task 绕过客户端签名预检，让错误发生在 worker 端，产生真实 FAILURE 状态
    result = celery_worker.send_task("app.workers.tasks.send_sms", kwargs={"unexpected_arg": 1})
    value = result.get(timeout=30, propagate=False)
    assert result.failed() is True
    assert isinstance(result.result, TypeError)
    assert value is None or isinstance(value, TypeError)


def test_celery_result_backend_roundtrip(celery_worker):
    """结果经真实 result backend 写读：状态机 ready→success 可复核。"""
    from app.workers.tasks import send_sms

    result = send_sms.delay("13900000002", "135790")
    assert result.get(timeout=30)["sent"] is True
    # 重新按 task_id 取结果（跨调用读 backend）
    from app.workers.celery_app import celery_app

    restored = celery_app.AsyncResult(result.id)
    assert restored.successful() is True
    assert restored.result == {"phone": "13900000002", "sent": True}
