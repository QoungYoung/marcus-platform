# -*- coding: utf-8 -*-
"""调度器「失败自动重试」单测（2026-09-16 新实现）。

**背景**：config 里 `settings.retry{enabled,max_attempts,delay_seconds}` 一直是**死配置**——
`SchedulerService` 里没有任何 retry/requeue 逻辑，而 `wolf_eod` 就绪闸的注释写着
"没就绪就非 0 退出交给调度器重试" → 实际当天直接终止（盘后 EOD 批一旦迟到，那天就没有产物）。

**口径（本文件锁死）**：
  · 只重试 `type=script`；`pi_trade/pi_reflect/golden_pit/golden_pit_dca` 明确不重跑；
  · **必须任务级 opt-in**（`retry: {enabled: true}`），settings.retry 只当总开关+默认参数
    —— 新任务（尤其会下单的）不会被自动重跑，避免"重复下单"这类比少跑一次严重得多的错；
  · 重试走 `_execute_task` 同一路径（执行记录/通知/时间窗校验照旧），排期失败不影响主流程。
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

# 注意：`app.services/__init__.py` 里 `from .scheduler_service import scheduler_service`
# 会把包属性 `app.services.scheduler_service` 覆盖成**单例实例** → 必须用 importlib 取模块本体
import importlib  # noqa: E402
S = importlib.import_module("app.services.scheduler_service")


def _svc(tmp_path, retry=None, job_timeout=30):
    """造一个隔离的 SchedulerService（不走单例、不读真配置、不连库）。"""
    svc = object.__new__(S.SchedulerService)
    svc._initialized = True
    svc.config_path = tmp_path / "tasks.yaml"
    svc.tasks = {}
    svc.executions = {}
    svc.workspace = str(tmp_path)
    svc.settings = {"job_timeout": job_timeout,
                    "retry": retry if retry is not None else
                    {"enabled": True, "max_attempts": 3, "delay_seconds": 60}}
    svc._qq_notifier = None
    svc._qq_recipient = None
    svc._reflect_notified = False
    svc.scheduler = BackgroundScheduler(
        jobstores={'default': MemoryJobStore()},
        job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 300},
        timezone='Asia/Shanghai',
    )
    svc.scheduler.start()
    return svc


@pytest.fixture
def svc(tmp_path):
    s = _svc(tmp_path)
    yield s
    try:
        s.scheduler.shutdown(wait=False)
    except Exception:
        pass


def _task(tid="t_data", **kw):
    d = {"id": tid, "name": tid, "type": "script", "script": {"path": "jobs/x.py", "args": []},
         "notifications": {}, "schedule": {"type": "cron", "expr": "0 18 * * *"}}
    d.update(kw)
    return S.TaskConfig.from_dict(d)


def _fake_run(returncode=2, stdout="", stderr="boom"):
    def _f(*a, **k):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return _f


# ── 策略：opt-in + 类型白名单 ────────────────────────────────────────
def test_policy_requires_task_level_optin(tmp_path):
    s = _svc(tmp_path)
    try:
        assert s._retry_policy(_task())["enabled"] is False          # 没 opt-in → 不重试
        assert s._retry_policy(_task(retry={"enabled": True}))["enabled"] is True
    finally:
        s.scheduler.shutdown(wait=False)


def test_policy_master_switch_off(tmp_path):
    s = _svc(tmp_path, retry={"enabled": False, "max_attempts": 3})
    try:
        assert s._retry_policy(_task(retry={"enabled": True}))["enabled"] is False
    finally:
        s.scheduler.shutdown(wait=False)


def test_policy_pi_trade_never_retries(tmp_path):
    """pi_trade 有下单副作用 → 即使显式 opt-in 也不重跑。"""
    s = _svc(tmp_path)
    try:
        p = s._retry_policy(_task(type="pi_trade", retry={"enabled": True}))
        assert p["enabled"] is False
    finally:
        s.scheduler.shutdown(wait=False)


def test_policy_task_overrides_defaults(tmp_path):
    s = _svc(tmp_path)
    try:
        p = s._retry_policy(_task(retry={"enabled": True, "max_attempts": 5, "delay_seconds": 5}))
        assert (p["max_attempts"], p["delay_seconds"]) == (5, 5)
        p2 = s._retry_policy(_task(retry={"enabled": True}))
        assert (p2["max_attempts"], p2["delay_seconds"]) == (3, 60)      # 回落 settings
    finally:
        s.scheduler.shutdown(wait=False)


# ── 排期 ───────────────────────────────────────────────────────────
def test_schedule_retry_adds_one_shot_job(svc):
    t = _task(retry={"enabled": True, "delay_seconds": 300})
    assert svc._schedule_retry(t, 2, 300, manual=False, max_attempts=3) is True
    job = svc.scheduler.get_job("retry::t_data::2")
    assert job is not None and isinstance(job.trigger, DateTrigger)
    assert job.kwargs.get("_attempt") == 2 and list(job.args) == ["t_data"]
    assert "2/3" in job.name


def test_schedule_retry_refuses_when_scheduler_not_running(tmp_path):
    s = _svc(tmp_path)
    s.scheduler.shutdown(wait=False)          # STATE_STOPPED
    t = _task(retry={"enabled": True})
    assert s._schedule_retry(t, 2, 300, max_attempts=3) is False


# ── 端到端（打桩 subprocess）────────────────────────────────────────
def _run_task(svc, task, monkeypatch, returncode):
    monkeypatch.setattr(S.subprocess, "run", _fake_run(returncode=returncode))
    svc.tasks[task.id] = task
    svc._execute_task(task.id, manual=False, _attempt=1)
    ex = list(svc.executions.values())[-1]
    return ex


def test_failed_script_task_is_rescheduled(svc, monkeypatch):
    t = _task(retry={"enabled": True, "delay_seconds": 300})
    ex = _run_task(svc, t, monkeypatch, returncode=2)
    assert ex.status == S.JobStatus.FAILED.value and ex.return_code == 2
    assert ex.attempt == 1 and ex.max_attempts == 3
    assert ex.will_retry is True and ex.retry_in == 300
    assert svc.scheduler.get_job("retry::t_data::2") is not None
    # 执行记录落盘要带上重试元信息（给调度历史/UI 读）
    log = svc._get_workspace_path() / "logs" / t.id / f"{ex.id}.json"
    import json
    data = json.loads(log.read_text(encoding="utf-8"))
    assert data["will_retry"] is True and data["attempt"] == 1 and data["max_attempts"] == 3


def test_success_does_not_reschedule(svc, monkeypatch):
    t = _task(retry={"enabled": True, "delay_seconds": 300})
    ex = _run_task(svc, t, monkeypatch, returncode=0)
    assert ex.status == S.JobStatus.SUCCESS.value and ex.will_retry is False
    assert svc.scheduler.get_job("retry::t_data::2") is None


def test_no_optin_never_reschedules(svc, monkeypatch):
    t = _task()                                   # 没有 retry 覆盖
    ex = _run_task(svc, t, monkeypatch, returncode=2)
    assert ex.status == S.JobStatus.FAILED.value and ex.will_retry is False
    assert svc.scheduler.get_job("retry::t_data::2") is None


def test_attempts_exhausted_stops_retrying(svc, monkeypatch):
    t = _task(retry={"enabled": True, "delay_seconds": 300})
    monkeypatch.setattr(S.subprocess, "run", _fake_run(returncode=2))
    svc.tasks[t.id] = t
    svc._execute_task(t.id, manual=False, _attempt=3)      # 最后一次尝试
    ex = list(svc.executions.values())[-1]
    assert ex.will_retry is False and ex.retry_in == 0
    assert svc.scheduler.get_job("retry::t_data::4") is None


# ── 通知文案 ───────────────────────────────────────────────────────
def test_notification_mentions_retry(svc):
    sent = []
    svc._qq_notifier = lambda msg, to=None: sent.append(msg)
    t = _task(retry={"enabled": True}, notifications={"on_failure": True, "channels": ["qqbot"]})
    ex = S.JobExecution(id="e1", task_id=t.id, task_name=t.name, status=S.JobStatus.FAILED.value,
                        started_at=datetime.now(), finished_at=datetime.now(),
                        error="rc=2", return_code=2, attempt=1, max_attempts=3,
                        will_retry=True, retry_in=60)
    svc._send_notifications(t, ex)
    assert sent and "60s 后再跑一次" in sent[0] and "2/3" in sent[0]


def test_notification_mentions_exhausted(svc):
    sent = []
    svc._qq_notifier = lambda msg, to=None: sent.append(msg)
    t = _task(retry={"enabled": True}, notifications={"on_failure": True, "channels": ["qqbot"]})
    ex = S.JobExecution(id="e2", task_id=t.id, task_name=t.name, status=S.JobStatus.FAILED.value,
                        started_at=datetime.now(), finished_at=datetime.now(),
                        error="rc=2", return_code=2, attempt=3, max_attempts=3)
    svc._send_notifications(t, ex)
    assert sent and "已用尽" in sent[0]


# ── 配置一致性（防止误 opt-in 到会下单的任务）────────────────────────
def test_config_opts_in_only_eod_data_tasks():
    import yaml
    d = yaml.safe_load((REPO_ROOT / "config" / "tasks.yaml").read_text(encoding="utf-8"))
    opted = {t["id"] for t in d["tasks"] if (t.get("retry") or {}).get("enabled")}
    expected = {"wolf_limit_ladder_scan", "wolf_review_score_run", "wolf_theme_resilience",
                "wolf_index_futures", "wolf_volume_gate", "wolf_mainline_select",
                "daily_decision", "daily_archive", "daily_decision_am"}
    assert opted == expected, "重试 opt-in 集合变了——会下单的 script 任务不得 opt-in"
    assert d["settings"]["retry"]["enabled"] is True
    # 会下单/有一次性副作用的必须不在名单里
    for tid in ("mainline_open_buy", "rotation_switch_agent_morning",
                "rotation_switch_agent_afternoon", "rotation_switch_arm", "daily_snapshot"):
        assert tid not in opted
