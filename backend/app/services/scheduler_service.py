# -*- coding: utf-8 -*-
"""
Scheduler Service - 替代 OpenClaw 的任务调度系统
基于 APScheduler + YAML 配置
"""
import os
import re
import sys
import json
import yaml
import logging
import subprocess
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import threading
import uuid
from app.config import get_settings

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 实时止损监控（延迟导入避免循环依赖）
_stop_loss_monitor = None


def _get_monitor():
    global _stop_loss_monitor
    if _stop_loss_monitor is None:
        try:
            from app.services.stop_loss_monitor import get_stop_loss_monitor
            _stop_loss_monitor = get_stop_loss_monitor()
        except Exception as e:
            logger.warning(f"[StopLoss] 监控模块加载失败: {e}")
    return _stop_loss_monitor


class JobStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class TaskConfig:
    """任务配置"""
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    type: str = "script"  # "script" | "pi_trade" — pi_trade 由 Pi Agent 自主决策执行
    schedule: Dict[str, Any] = field(default_factory=dict)
    script: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    notifications: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    pi_prompt: str = ""  # pi_trade 类型使用的 context 提示（如 "early""late_morning""afternoon""closing"）

    @classmethod
    def from_dict(cls, data: Dict) -> 'TaskConfig':
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            description=data.get('description', ''),
            enabled=data.get('enabled', True),
            type=data.get('type', 'script'),
            schedule=data.get('schedule', {}),
            script=data.get('script', {}),
            output=data.get('output', {}),
            notifications=data.get('notifications', {}),
            depends_on=data.get('depends_on', []),
            pi_prompt=data.get('pi_prompt', ''),
        )


@dataclass
class JobExecution:
    """任务执行记录"""
    id: str
    task_id: str
    task_name: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    output: str = ""
    error: str = ""
    return_code: int = 0


class SchedulerService:
    """调度器服务"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.config_path = Path(__file__).parent.parent.parent / "config" / "tasks.yaml"
        self.tasks: Dict[str, TaskConfig] = {}
        self.executions: Dict[str, JobExecution] = {}
        self.workspace = ""
        self.settings: Dict = {}

        # QQ 通知
        self._qq_notifier = None
        self._qq_recipient = None

        # APScheduler
        self.scheduler = BackgroundScheduler(
            jobstores={'default': MemoryJobStore()},
            job_defaults={
                'coalesce': True,
                'max_instances': 1,
                'misfire_grace_time': 300,
            }
        )

        # Event listeners
        self.scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
        self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)

        self._load_config()
        self._load_execution_history()

    def set_qq_notifier(self, notifier, recipient: str = None):
        """设置 QQ 通知函数"""
        self._qq_notifier = notifier
        self._qq_recipient = recipient

    def _load_execution_history(self):
        """从日志文件加载历史执行记录"""
        try:
            log_dir = self._get_workspace_path() / "logs"
            if not log_dir.exists():
                return

            for log_file in sorted(log_dir.glob("scheduler_*.jsonl")):
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                exec_id = data.get('execution_id', '')
                                task_id = data.get('task_id', '')
                                started = data.get('started_at', '')
                                finished = data.get('finished_at', '')
                                status = data.get('status', '')
                                return_code = data.get('return_code', 0)
                                output = data.get('output', '')
                                error = data.get('error', '')

                                from datetime import datetime
                                execution = JobExecution(
                                    id=exec_id,
                                    task_id=task_id,
                                    task_name=data.get('task_name', ''),
                                    status=status,
                                    started_at=datetime.fromisoformat(started) if started else datetime.now(),
                                    finished_at=datetime.fromisoformat(finished) if finished else None,
                                    output=output,
                                    error=error,
                                    return_code=return_code,
                                )
                                self.executions[exec_id] = execution
                            except Exception:
                                continue
                except Exception:
                    continue

            logger.info(f"Loaded {len(self.executions)} execution records from history")
        except Exception as e:
            logger.warning(f"Failed to load execution history: {e}")

    def _load_config(self):
        """加载任务配置"""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            return

        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Load settings
        self.settings = config.get('settings', {})
        # Use config.py's workspace_path (respected by env var MARCUS_WORKSPACE)
        self.workspace = str(get_settings().workspace_path)

        # Load tasks
        tasks_data = config.get('tasks', [])
        self.tasks = {}
        for task_data in tasks_data:
            task = TaskConfig.from_dict(task_data)
            self.tasks[task.id] = task

        logger.info(f"Loaded {len(self.tasks)} tasks from config")

    def reload_config(self):
        """重新加载配置"""
        self.stop()
        self._load_config()
        self.start()

    def start(self):
        """启动调度器"""
        if self.scheduler.running:
            logger.info("Scheduler already running")
            return

        # Add jobs from config
        for task_id, task in self.tasks.items():
            if task.enabled:
                self._add_job(task)

        self.scheduler.start()
        
        # ── 启动时关键模组健康检查 ──
        monitor = _get_monitor()
        if monitor:
            logger.info(f"✅ 止损监控模块已加载（当前未启用，30秒轮询会触发雪球IP限流）")
        else:
            logger.warning("⚠️ 止损监控模块加载失败，东睦式滑点风险仅靠定时窗口保护")
        
        # ── 东财缓存清理（保留3天）──
        try:
            from core.utils.eastmoney_cache import get_em_cache
            deleted = get_em_cache().prune(keep_days=3)
            if deleted > 0:
                logger.info(f"🗑️ 东财缓存清理: 删除 {deleted} 个过期文件")
        except Exception:
            pass
        
        logger.info(f"Scheduler started with {len(self.tasks)} tasks")

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.pause()  # 先暂停，防止新任务触发
            self.scheduler.shutdown(wait=True)  # 等待已提交任务完成
            logger.info("Scheduler stopped")

    def _get_workspace_path(self) -> Path:
        """获取 workspace 路径"""
        return Path(self.workspace)

    def _add_job(self, task: TaskConfig):
        """添加任务到调度器"""
        if task.schedule.get('type') != 'cron':
            logger.warning(f"Task {task.id}: Only cron schedule is supported")
            return

        expr = task.schedule.get('expr', '')
        timezone = task.schedule.get('timezone', 'Asia/Shanghai')

        try:
            # Parse cron expression: "35 9,10,13 * * 1-5"
            parts = expr.split()
            if len(parts) != 5:
                logger.error(f"Invalid cron expression: {expr}")
                return

            minute, hour, day, month, day_of_week = parts

            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=timezone,
            )

            self.scheduler.add_job(
                func=self._execute_task,
                trigger=trigger,
                id=task.id,
                name=task.name,
                args=[task.id],
                replace_existing=True,
            )

            logger.info(f"Added job: {task.id} ({task.name}) - {expr}")

        except Exception as e:
            logger.error(f"Failed to add job {task.id}: {e}")

    def _execute_task(self, task_id: str, manual: bool = False):
        """执行任务
        
        Args:
            task_id: 任务ID
            manual: 是否手动触发（True 时跳过时间窗口校验）
        """
        task = self.tasks.get(task_id)
        if not task:
            logger.error(f"Task not found: {task_id}")
            return

        # 交易日检查：盘前扫描/盘中扫描/自动交易/每日复盘 仅交易日执行
        TRADE_DAY_ONLY_TASKS = {
            'pre_market_scan', 'market_scan',
            'auto_trade_morning', 'auto_trade_late_morning',
            'auto_trade_afternoon', 'auto_trade_closing',
            'daily_review', 'weekly_reflect',
        }
        if task_id in TRADE_DAY_ONLY_TASKS:
            try:
                sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "core"))
                from utils.trade_day_utils import is_today_trade_day
            except ImportError:
                # 无法导入时降级：简单跳过周末
                weekday = datetime.now().strftime('%w')
                if weekday in ['0', '6']:
                    logger.info(f"[{task_id}] 周末休市，跳过任务: {task.name}")
                    return
                # 保守策略：允许执行
                logger.warning(f"[{task_id}] 交易日检测模块不可用，跳过检查")
            else:
                is_trade, reason = is_today_trade_day()
                if not is_trade:
                    logger.info(f"[{task_id}] 非交易日({reason})，跳过任务: {task.name}")
                    return
                logger.info(f"[{task_id}] 交易日确认: {reason}")

        # 时间窗口检查（仅定时触发时生效，手动触发跳过）
        TASK_TIME_WINDOWS = {
            'pre_market_scan':        ('盘前', 8*60,  9*60+25),
            'market_scan':            ('盘中', 9*60+25, 15*60+5),
            'auto_trade_morning':     ('早盘', 9*60+25, 11*60+35),
            'auto_trade_late_morning':('午前', 9*60+25, 11*60+35),
            'auto_trade_afternoon':   ('午后', 12*60+55, 15*60+5),
            'auto_trade_closing':     ('尾盘', 12*60+55, 15*60+5),
            'daily_review':           ('复盘', 15*60,  18*60),
        }
        if manual:
            if task_id in TASK_TIME_WINDOWS:
                logger.info(f"[{task_id}] 手动触发，跳过时间窗口校验")
        else:
            now = datetime.now()
            now_time = now.hour * 60 + now.minute
            window = TASK_TIME_WINDOWS.get(task_id)
            if window:
                window_name, start_min, end_min = window
                if now_time < start_min or now_time > end_min:
                    logger.info(
                        f"[{task_id}] 超出{window_name}时间窗口 "
                        f"({start_min//60:02d}:{start_min%60:02d}-{end_min//60:02d}:{end_min%60:02d})，"
                        f"当前 {now.strftime('%H:%M')}，跳过任务: {task.name}"
                    )
                    return

        execution_id = str(uuid.uuid4())[:8]
        execution = JobExecution(
            id=execution_id,
            task_id=task_id,
            task_name=task.name,
            status=JobStatus.RUNNING.value,
            started_at=datetime.now(),
        )
        self.executions[execution_id] = execution

        logger.info(f"[{execution_id}] Starting task: {task.name} (type={task.type})")

        # === Pi 自主交易模式 ===
        if task.type == 'pi_trade':
            # ── 止损监控生命周期管理（暂不启用：30秒轮询会导致雪球封IP）──
            # 开启方式：取消下方注释即可
            # is_first_trade = 'morning' in task.id and 'late' not in task.id
            # is_closing = 'closing' in task.id
            # monitor = _get_monitor()
            # if is_first_trade and monitor and not monitor.is_running():
            #     monitor.start()
            #     logger.info(f"[{execution_id}] 🟢 止损监控已随首个交易任务启动")
            
            try:
                output = self._execute_pi_trade(task, execution_id)
                execution.status = JobStatus.SUCCESS.value
                execution.output = output
                execution.return_code = 0
            except Exception as e:
                execution.status = JobStatus.FAILED.value
                execution.error = f"{str(e)}\n{traceback.format_exc()}"
                logger.error(f"[{execution_id}] Pi trade failed: {e}")
            finally:
                execution.finished_at = datetime.now()
                self._save_execution_log(execution)
                self._send_notifications(task, execution)
                # ── 止损监控停止（暂不启用）──
                # if is_closing and monitor and monitor.is_running():
                #     monitor.stop()
                #     logger.info(f"[{execution_id}] 🔴 止损监控已随尾盘任务停止")
                
                # ── 极端流出日扫描（遗漏#1）──
                # 每次扫描结束后记录全市场主力净流出
                try:
                    from app.core.trading.marcus_trade import MarcusVNPyExecutor
                    executor = MarcusVNPyExecutor()
                    outflow = executor._get_market_outflow_billion()
                    scan_result = executor.record_market_outflow_scan(outflow)
                    
                    scan_label = task.id.split('_')[-1] if '_' in task.id else task.id
                    if outflow > 800:
                        logger.warning(
                            f"[{execution_id}] [extreme_outflow] {scan_label} 轮次: "
                            f"主力净流出 {outflow:.0f}亿 | 连续 {scan_result['scans']}/3 轮"
                        )
                    
                    # 尾盘触发时执行强制减仓
                    if scan_result['triggered'] and is_closing:
                        logger.warning(f"[{execution_id}] ⚠️ 极端流出防御触发！执行强制减仓50%")
                        defense_results = executor.execute_extreme_outflow_defense()
                        if defense_results:
                            logger.warning(
                                f"[{execution_id}] [extreme_outflow] 已强制减仓: "
                                f"{', '.join(r['symbol'] + ' ' + str(r['sold_volume']) + '股' for r in defense_results)}"
                            )
                        else:
                            logger.warning(f"[{execution_id}] [extreme_outflow] 无可减仓持仓（全部T+1锁定）")
                except Exception as e:
                    logger.debug(f"[{execution_id}] [extreme_outflow] 扫描跳过: {e}")
            return

        # === Pi 周度反思模式 ===
        if task.type == 'pi_reflect':
            try:
                output = self._execute_pi_reflect(task, execution_id)
                execution.status = JobStatus.SUCCESS.value
                execution.output = output
                execution.return_code = 0
            except Exception as e:
                execution.status = JobStatus.FAILED.value
                execution.error = f"{str(e)}\n{traceback.format_exc()}"
                logger.error(f"[{execution_id}] Pi reflect failed: {e}")
            finally:
                execution.finished_at = datetime.now()
                self._save_execution_log(execution)
                self._send_notifications(task, execution)
            return

        try:
            # Prepare script path
            script_path = self._get_workspace_path() / task.script.get('path', '')
            logger.info(f"[{execution_id}] Script path: {script_path}")
            logger.info(f"[{execution_id}] Script exists: {script_path.exists()}")

            cwd = self._get_workspace_path()
            logger.info(f"[{execution_id}] Working dir: {cwd}")
            args = task.script.get('args', [])
            timeout = self.settings.get('job_timeout', 900)

            # Build command
            cmd = [sys.executable, str(script_path)] + args
            logger.info(f"[{execution_id}] Command: {' '.join(cmd)}")

            # Execute with UTF-8 encoding
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUNBUFFERED'] = '1'  # Force unbuffered output
            logger.info(f"[{execution_id}] About to run subprocess...")

            # Debug: write command to batch file and run it
            debug_dir = self._get_workspace_path() / "logs" / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"{execution_id}_debug.txt"

            # Write full output to debug file
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(f"Command: {' '.join(cmd)}\n")
                f.write(f"CWD: {cwd}\n")
                f.write(f"Python: {sys.executable}\n\n")

            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                env=env,
            )

            # Write results to debug file
            with open(debug_file, 'a', encoding='utf-8') as f:
                f.write(f"Return code: {result.returncode}\n")
                f.write(f"stdout length: {len(result.stdout) if result.stdout else 0}\n")
                f.write(f"stderr length: {len(result.stderr) if result.stderr else 0}\n\n")
                f.write("=== STDOUT ===\n")
                f.write(result.stdout if result.stdout else "(empty)")
                f.write("\n\n=== STDERR ===\n")
                f.write(result.stderr if result.stderr else "(empty)")

            logger.info(f"[{execution_id}] Subprocess finished, returncode={result.returncode}")
            logger.info(f"[{execution_id}] Debug log: {debug_file}")

            output = result.stdout if result.stdout else ""
            error = result.stderr if result.stderr else ""

            # Log output for debugging
            logger.info(f"[{execution_id}] stdout length: {len(output)}")
            logger.info(f"[{execution_id}] stderr length: {len(error)}")
            if output:
                logger.info(f"[{execution_id}] stdout (first 500 chars): {output[:500]}")
            return_code = result.returncode

            logger.info(f"[{execution_id}] Return code: {return_code}")
            logger.info(f"[{execution_id}] Output length: {len(output)}, Error length: {len(error)}")

            if return_code == 0:
                execution.status = JobStatus.SUCCESS.value
                execution.error = error  # 保存 stderr 即使成功
                logger.info(f"[{execution_id}] Task {task.name} completed successfully")
            else:
                execution.status = JobStatus.FAILED.value
                execution.error = error
                logger.error(f"[{execution_id}] Task {task.name} failed: {error}")

            execution.output = output
            execution.return_code = return_code

        except subprocess.TimeoutExpired:
            execution.status = JobStatus.FAILED.value
            execution.error = f"Task timeout after {timeout} seconds"
            logger.error(f"[{execution_id}] Task {task.name} timeout")

        except Exception as e:
            execution.status = JobStatus.FAILED.value
            execution.error = f"{str(e)}\n{traceback.format_exc()}"
            logger.error(f"[{execution_id}] Task {task.name} error: {e}")

        finally:
            execution.finished_at = datetime.now()
            self._save_execution_log(execution)
            self._send_notifications(task, execution)

    def _save_execution_log(self, execution: JobExecution):
        """保存执行日志"""
        try:
            log_dir = self._get_workspace_path() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            log_file = log_dir / f"scheduler_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
            log_data = {
                'execution_id': execution.id,
                'task_id': execution.task_id,
                'task_name': execution.task_name,
                'status': execution.status,
                'started_at': execution.started_at.isoformat(),
                'finished_at': execution.finished_at.isoformat() if execution.finished_at else None,
                'return_code': execution.return_code,
                'output': execution.output if execution.output else "",
                'error': execution.error if execution.error else "",
            }
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')

            # Also save individual task log files for easier viewing
            task_log_dir = log_dir / execution.task_id
            task_log_dir.mkdir(exist_ok=True, parents=True)
            task_log_file = task_log_dir / f"{execution.id}.json"
            with open(task_log_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save execution log: {e}")

    def _send_notifications(self, task: TaskConfig, execution: JobExecution):
        """发送通知"""
        notifications = task.notifications
        channels = notifications.get('channels', [])

        if execution.status == JobStatus.SUCCESS.value and not notifications.get('on_success'):
            return
        if execution.status == JobStatus.FAILED.value and not notifications.get('on_failure'):
            return

        # QQ Bot 通知
        if 'qqbot' in channels:
            if self._qq_notifier:
                use_pi = notifications.get('pi_analysis', False)
                
                # === pi_trade / pi_reflect 模式：output 已经是 Pi 报告，直接推送 ===
                if task.type in ('pi_trade', 'pi_reflect') and execution.output and execution.status == JobStatus.SUCCESS.value:
                    try:
                        # 去掉 SIGNAL 行再发送
                        clean_output = re.sub(r'\n?SIGNAL:.*', '', execution.output).strip()
                        self._qq_notifier(clean_output, self._qq_recipient)
                        logger.info(f"QQ Pi-report sent for {task.name} (type={task.type})")
                        return
                    except Exception as e:
                        logger.error(f"Failed to send Pi-report: {e}")
                
                if use_pi and execution.output and execution.status == JobStatus.SUCCESS.value:
                    # 将扫描结果发给 Pi 分析，生成报告
                    report = self._call_pi_analysis(task.name, execution.output)
                    if report:
                        try:
                            self._qq_notifier(report, self._qq_recipient)
                            logger.info(f"QQ Pi-report sent for {task.name}")
                            return
                        except Exception as e:
                            logger.error(f"Failed to send Pi report: {e}")
                
                # 回退：简单通知
                emoji = "✅" if execution.status == JobStatus.SUCCESS.value else "❌"
                lines = [
                    f"{emoji} Marcus 任务通知",
                    f"任务: {task.name}",
                    f"状态: {execution.status}",
                    f"时间: {execution.finished_at.strftime('%H:%M:%S')}" if execution.finished_at else "",
                ]
                if execution.output:
                    output_preview = execution.output[:500]
                    if len(execution.output) > 500:
                        output_preview += "\n... (已截断)"
                    lines.append(f"\n输出:\n{output_preview}")
                if execution.error:
                    lines.append(f"\n错误: {execution.error[:300]}")

                message = "\n".join(filter(None, lines))
                try:
                    self._qq_notifier(message, self._qq_recipient)
                    logger.info(f"QQ notification sent for {task.name}")
                except Exception as e:
                    logger.error(f"Failed to send QQ notification: {e}")
            else:
                logger.info(f"QQ notification not available for {task.name}")

    def _get_recent_pi_review(self) -> Optional[dict]:
        """
        获取前一日最后一条有效 Pi 分析记录（排除新闻/晚报类任务）。

        优先级：每日复盘 > 盘中扫描 > 其他非新闻任务
        跳过 task_name 含 "新闻" / "晚报" 的记录（偏情绪而非技术面立场）。

        Returns:
            dict | None: {"stance": "red", "position_limit": 20, "reason": "...", "task_name": "..."}
        """
        try:
            from datetime import timedelta
            prev_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            log_dir = self._get_workspace_path() / "memory" / "pi-analysis-logs"
            log_file = log_dir / f"{prev_date}-analysis.jsonl"

            if not log_file.exists():
                logger.info(f"[pre_market] No pi-analysis log for {prev_date}")
                return None

            records = []
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        records.append(rec)
                    except json.JSONDecodeError:
                        continue

            if not records:
                return None

            # 倒序遍历，按优先级筛选
            # 第一遍：找每日复盘
            for rec in reversed(records):
                task = rec.get('task_name', '')
                if '复盘' in task and '新闻' not in task and '晚报' not in task:
                    logger.info(f"[pre_market] Found daily_review from {prev_date}: "
                                f"stance={rec.get('stance')}, limit={rec.get('position_limit')}%")
                    return rec

            # 第二遍：找盘中扫描（跳过新闻/晚报）
            for rec in reversed(records):
                task = rec.get('task_name', '')
                if '新闻' in task or '晚报' in task:
                    continue
                logger.info(f"[pre_market] Found scan review from {prev_date}: "
                            f"stance={rec.get('stance')}, limit={rec.get('position_limit')}%, task={task}")
                return rec

            # 全是新闻/晚报
            logger.info(f"[pre_market] {prev_date} only has news/evening reports, no technical review")
            return None

        except Exception as e:
            logger.error(f"[pre_market] Failed to read recent pi review: {e}")
            return None

    def _call_pi_analysis(self, task_name: str, output: str) -> str:
        """将扫描结果发送给 Pi Server，获取分析报告并写入策略链"""
        import urllib.request, json as _json, ssl, re
        try:
            # 根据任务类型生成不同的分析指令
            prev_review_context = ""  # 默认为空，仅盘前分支使用
            if '盘前' in task_name:
                report_guide = (
                    "报告结构：隔夜外盘 → A50/汇率 → 板块催化 → 今日展望。"
                )
                # === 盘前加载前日最终复盘信号 ===
                prev_review = self._get_recent_pi_review()
                prev_review_context = ""
                if prev_review:
                    prev_stance = prev_review.get('stance', 'yellow')
                    prev_limit = prev_review.get('position_limit', 60)
                    prev_reason = prev_review.get('reason', '')
                    prev_task = prev_review.get('task_name', '未知')
                    prev_review_context = (
                        f"\n\n⚠️ 前日最终复盘信号（{prev_task}）：\n"
                        f"立场：{prev_stance} | 仓位上限：{prev_limit}%\n"
                        f"理由：{prev_reason}\n"
                        f"盘前约束：若上调仓位需额外确认，盘前立场不得超过前日复盘仓位的 2 倍。\n"
                        f"若前日最终为 red 或催化强度 < 20，盘前立场不得超出 yellow/40%。"
                    )
                    if prev_stance == 'red' or prev_limit <= 20:
                        prev_review_context += (
                            f"\n🔴 前日为 red/仓位≤20%，今日盘前必须以 yellow/≤40% 为上限，"
                            f"不得给出 green 立场，仓位不得超过 {min(prev_limit * 2, 40)}%。"
                        )
            elif '交易' in task_name:
                report_guide = (
                    "报告结构：成交概况 → 盈亏分析 → 持仓变化 → 策略评估。"
                    "对比盘前策略与实际执行差异，评估右侧信号有效性。"
                )
            elif '晚报' in task_name:
                # 新闻晚报 — 纯 DB 模式，不重新采集
                report_guide = (
                    "报告结构：全天新闻回顾 → 情绪倾向 → 热点板块关联 → 明日关注。\n"
                    "注意：finance_new=0 和 total_new=0 是正常的——晚报不重新采集新闻，"
                    "只读取数据库中白天已入库的新闻做汇总。"
                    "真正有意义的数据是 db_total_today（今日累计入库新闻数）。"
                    "如果 db_total_today=0，说明白天采集系统未运行，请在报告中提醒用户排查。\n"
                    "侧重新闻对个股和板块的潜在影响，不重复市场数据。"
                )
            elif '新闻' in task_name:
                report_guide = (
                    "报告结构：今日重大新闻 → 情绪倾向 → 热点板块关联 → 明日关注。"
                    "侧重新闻对个股和板块的潜在影响，不重复市场数据。"
                )
            else:
                report_guide = (
                    "报告结构：市场概况 → 板块资金 → 指数复盘 → 风险关注点。"
                )

            prompt = (
                f"以下是 {task_name} 定时任务的执行结果。\n"
                f"请基于「右侧交易」框架对此数据进行分析，"
                f"整合为简洁专业的任务报告。\n"
                f"{report_guide}"
                f"控制在 800 字以内。\n"
                f"{prev_review_context}\n"
                f"最后，用单独一行给出你的策略判断：\n"
                f"SIGNAL: <green|yellow|red> POSITION:<0-100> REASON:<一句话理由>\n"
                f"其中 green=激进 yellow=谨慎 red=观望。"
                f"\n\n=== 任务输出 ===\n{output}"
            )
            payload = _json.dumps({
                "message": prompt,
                "session_id": f"report_{task_name}_{datetime.now().strftime('%Y%m%d')}"
            }).encode("utf-8")

            pi_url = get_settings().PI_SERVER_URL
            req = urllib.request.Request(
                pi_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
                reply = data.get("reply", "")
                if not reply:
                    return ""

                logger.info(f"Pi analysis done for {task_name} ({len(reply)} chars)")

                # 解析策略信号并写入策略链
                signal_match = re.search(
                    r'SIGNAL:\s*(green|yellow|red)\s+POSITION:\s*(\d+)\s*REASON:\s*(.+)',
                    reply, re.IGNORECASE
                )
                stance = 'yellow'
                position_limit = 60
                reason_str = ''
                if signal_match:
                    try:
                        stance = signal_match.group(1).lower()
                        position_limit = int(signal_match.group(2))
                        reason_str = signal_match.group(3).strip()
                        from core.utils.strategy_chain import StrategyChain
                        chain = StrategyChain()
                        chain.set_pi_confirmation(
                            stance=stance,
                            position_limit=position_limit,
                            reason=reason_str,
                        )
                        logger.info(
                            f"Pi signal written: {signal_match.group(1)} "
                            f"limit={signal_match.group(2)}%"
                        )
                    except Exception as e:
                        logger.error(f"Failed to write Pi signal: {e}")

                # === 持久化 Pi 分析报告到 JSONL ===
                self._save_pi_analysis(task_name, reply, stance, position_limit, reason_str)

                # 返回报告时去掉 SIGNAL 行
                clean_reply = re.sub(r'\n?SIGNAL:.*', '', reply).strip()
                return clean_reply

        except Exception as e:
            logger.error(f"Pi analysis failed for {task_name}: {e}")
        return ""

    def _save_pi_analysis(self, task_name: str, reply: str, stance: str, position_limit: int, reason: str):
        """持久化 Pi 分析报告，供后续自动交易读取"""
        try:
            log_dir = self._get_workspace_path() / "memory" / "pi-analysis-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime('%Y-%m-%d')
            log_file = log_dir / f"{today}-analysis.jsonl"

            record = {
                "timestamp": datetime.now().isoformat(),
                "task_name": task_name,
                "stance": stance,
                "position_limit": position_limit,
                "reason": reason,
                "report": reply,  # 含 SIGNAL 行的完整报告
            }
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            logger.info(f"Pi analysis saved: {log_file}")
        except Exception as e:
            logger.error(f"Failed to save Pi analysis: {e}")

    def _save_trade_report(self, task_id: str, execution_id: str, reply: str, stance: str, position_limit: int, reason: str):
        """持久化 Pi 交易报告到 memory/trade-reports/，供周度反思等查询"""
        try:
            log_dir = self._get_workspace_path() / "memory" / "trade-reports"
            log_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime('%Y-%m-%d')
            log_file = log_dir / f"{today}-trades.jsonl"

            record = {
                "timestamp": datetime.now().isoformat(),
                "task_id": task_id,
                "execution_id": execution_id,
                "stance": stance,
                "position_limit": position_limit,
                "reason": reason,
                "report": reply,  # 含 SIGNAL 行的完整交易报告
            }
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            logger.info(f"Trade report saved: {log_file}")
        except Exception as e:
            logger.error(f"Failed to save trade report: {e}")

    def _save_weekly_reflect(self, start_date: str, end_date: str, reply: str,
                             stance: str, position_limit: int, reason: str):
        """持久化周度反思报告到 memory/weekly-reflect-logs/"""
        try:
            log_dir = self._get_workspace_path() / "memory" / "weekly-reflect-logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            now = datetime.now()
            filename = f"{start_date}_to_{end_date}-reflect"
            json_file = log_dir / f"{filename}.json"
            md_file = log_dir / f"{filename}.md"

            # ---- 结构化 JSON（完整原始回复） ----
            record = {
                "created_at": now.isoformat(),
                "date_range": {
                    "start": start_date,
                    "end": end_date,
                },
                "stance": stance,
                "position_limit": position_limit,
                "reason": reason,
                "report": reply,
            }
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

            # ---- 纯净 Markdown（去掉 SIGNAL 行） ----
            import re as _re
            clean_report = _re.sub(r'\n?SIGNAL:.*', '', reply).strip()
            md_content = (
                f"<!-- Marcus 周度反思报告 -->\n"
                f"<!-- 日期范围: {start_date} → {end_date} -->\n"
                f"<!-- 生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')} -->\n"
                f"<!-- 信号: {stance} | 仓位上限: {position_limit}% -->\n\n"
                f"{clean_report}\n"
            )
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(md_content)

            logger.info(f"Weekly reflect saved: {md_file} ({len(clean_report)} chars)")
        except Exception as e:
            logger.error(f"Failed to save weekly reflect: {e}")

    def _execute_pi_trade(self, task: TaskConfig, execution_id: str) -> str:
        """
        Pi 自主交易模式 —— 代替 auto_trade.py 脚本。

        流程：
        1. 根据时段构造交易指令
        2. 发送给 Pi Server /chat
        3. Pi 自主调用 get_latest_scan_report → 分析 → place_order 等工具
        4. Pi 返回交易报告
        5. 提取 SIGNAL 写入策略链
        6. 返回报告文本供 QQ 推送
        """
        import urllib.request, json as _json, ssl, re

        now = datetime.now()
        pi_prompt_context = task.pi_prompt or ''

        # 根据时段生成不同的交易指令
        if 'closing' in task.id or pi_prompt_context == 'closing':
            # 尾盘模式：只止损止盈，不开新仓
            trade_mode_instruction = (
                "现在是尾盘 14:30，进入 **closing 模式**。\n"
                "**严格禁止新开仓**。只执行以下操作：\n"
                "⚠️ A股 T+1 规则：今日买入的持仓今日不可卖出，跳过这些持仓！\n"
                "1. 对持仓逐只检查（跳过今日买入的），止损位触发则立即卖出\n"
                "2. 达到止盈目标的卖出（仅限昨日及之前买入的）\n"
                "3. 趋势破位的减仓 50%（排除 T+1 锁定持仓）\n"
                "4. 报告尾盘操作结果，标明哪些持仓因 T+1 锁定未操作"
            )
        elif 'early' in task.id or pi_prompt_context == 'early' or 'morning' in task.id or pi_prompt_context == 'morning':
            trade_mode_instruction = (
                "现在是早盘 9:35，进入 **建仓模式**。\n"
                "分析扫描报告中 寻找短线右侧强势标的，\n"
                "严格按照右侧交易 SOP 建仓。"
            )
        elif 'late' in task.id or pi_prompt_context == 'late_morning' or 'late_morning' in task.id:
            trade_mode_instruction = (
                "现在是午前 10:35，进入 **趋势确认+建仓模式**。\n"
                "1. 评估早盘建仓标的走势，不符合预期的及时止损\n"
                "2. 趋势确认的可以考虑加仓\n"
                "3. 扫描报告中新出现的强势标的，可按照右侧交易 SOP 新建仓"
            )
        elif 'afternoon' in task.id or pi_prompt_context == 'afternoon':
            trade_mode_instruction = (
                "现在是午后 13:35，进入 **午后修正+建仓模式**。\n"
                "1. 关注下午开盘方向，决定是否加仓或减仓\n"
                "2. 扫描报告中新出现的强势标的，可按照右侧交易 SOP 新建仓"
            )
        else:
            trade_mode_instruction = "请基于最新扫描报告执行自主交易决策。"

        # === 立场偏离检测：对比扫描系统当前立场 vs Pi 上一轮立场 ===
        stance_warning = ""
        try:
            # 1. 读取最新扫描报告的 stance
            scan_dir = self._get_workspace_path() / "memory" / "market-scan-logs"
            scan_file = None
            if scan_dir.exists():
                scan_files = sorted(scan_dir.glob("*-scans.jsonl"), reverse=True)
                if scan_files:
                    scan_file = scan_files[0]
            scan_stance = None
            scan_limit = None
            if scan_file and scan_file.exists():
                with open(scan_file, 'r', encoding='utf-8') as sf:
                    lines = [l.strip() for l in sf if l.strip()]
                if lines:
                    last_scan = json.loads(lines[-1])
                    scan_stance = (
                        last_scan.get('adjusted_strategy', {}).get('stance_code')
                        or last_scan.get('market_stance')
                        or last_scan.get('stance_code')
                    )
                    scan_limit = (
                        last_scan.get('adjusted_strategy', {}).get('position_limit')
                        or last_scan.get('position_limit')
                    )
            # 转换为统一 stance code
            def _normalize_stance(s):
                if not s:
                    return None
                s = str(s).lower()
                if 'green' in s or 'aggressive' in s or '🟢' in s:
                    return 'green'
                if 'red' in s or '🟡' not in s and 'hold' not in s and 'cautious' not in s:
                    if 'red' in s:
                        return 'red'
                if 'hold' in s or '⚪' in s:
                    return 'yellow'  # hold 归入 yellow 但属于低档
                if 'yellow' in s or 'cautious' in s or '🟡' in s:
                    return 'yellow'
                return 'yellow'

            scan_stance = _normalize_stance(scan_stance)

            # 2. 获取 Pi 上一轮立场
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            pi_conf = chain.get_pi_confirmation()
            pi_stance = pi_conf.get('stance', 'yellow')
            pi_limit = pi_conf.get('position_limit', 60)

            # 3. 计算偏离度
            STANCE_RANK = {'green': 3, 'yellow': 2, 'red': 1}
            if scan_stance and pi_stance:
                scan_rank = STANCE_RANK.get(scan_stance, 2)
                pi_rank = STANCE_RANK.get(pi_stance, 2)
                rank_diff = pi_rank - scan_rank

                if rank_diff >= 2:
                    # 扫描立场比 Pi 保守 2 档以上
                    stance_warning = (
                        f"\n🔴 **立场偏离警告（代码层检测）**\n"
                        f"扫描系统当前立场: {scan_stance}"
                        + (f"/{scan_limit}%" if scan_limit else "") + "\n"
                        f"Pi 上轮立场: {pi_stance}/{pi_limit}%\n"
                        f"偏离度: {rank_diff} 档 → **Pi 立场严重滞后！**\n"
                        f"⚠️ 本轮必须以降级处理：立场至少降至 {scan_stance}，"
                        f"仓位上限以扫描值为准。\n"
                    )
                    logger.info(f"[{execution_id}] [stance_divergence] scan={scan_stance}/{scan_limit}, "
                                f"pi={pi_stance}/{pi_limit}, diff={rank_diff}档, warning_injected=True")
                elif rank_diff == 1 and scan_limit and pi_limit and scan_limit < pi_limit * 0.5:
                    stance_warning = (
                        f"\n🟡 **立场偏离注意（代码层检测）**\n"
                        f"扫描系统仓位上限: {scan_limit}% | Pi 上轮仓位上限: {pi_limit}%\n"
                        f"扫描仓位 < Pi仓位的 50%，建议重新评估当前仓位上限。\n"
                    )
                    logger.info(f"[{execution_id}] [stance_divergence] limit_gap: scan={scan_limit}% vs pi={pi_limit}%, warning_injected=True")
                elif rank_diff == 0 and scan_limit and pi_limit and scan_limit < pi_limit * 0.35:
                    # 同档立场但仓位差距 > 3倍（如 Pi yellow/60% vs 扫描 yellow/20%）
                    stance_warning = (
                        f"\n🟡 **仓位级偏离警告（代码层检测）**\n"
                        f"立场同档（均为 {scan_stance}），但仓位上限严重脱节：\n"
                        f"扫描系统仓位上限: {scan_limit}% | Pi 上轮仓位上限: {pi_limit}%\n"
                        f"差距 {pi_limit / scan_limit:.1f}x → Pi 仓位上限严重虚高，以扫描值为准。\n"
                    )
                    logger.info(f"[{execution_id}] [stance_divergence] same_stance_limit_gap: "
                                f"scan={scan_limit}% vs pi={pi_limit}%, ratio={pi_limit/scan_limit:.1f}x, warning_injected=True")
        except Exception as e:
            logger.error(f"[{execution_id}] Stance divergence check failed (non-fatal): {e}")

        prompt = (
            f"{trade_mode_instruction}\n"
            f"{stance_warning}\n"
            f"请立即执行以下操作：\n"
            f"1. 调用 get_latest_scan_report 获取最新扫描报告\n"
            f"   （报告中 market_stance 为盘中扫描系统根据实时行情计算的市场立场，以此为准。pi_analysis 为历史参考）\n"
            f"2. 调用 get_portfolio 查看当前账户状态\n"
            f"3. 按右侧交易 SOP 选股分析\n"
            f"4. 执行交易（买入/卖出/调仓）\n"
            f"5. 输出完整交易报告\n\n"
            f"记住：你是 Marcus 右侧交易专家，严格遵循风控纪律。\n"
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        payload = _json.dumps({
            "message": prompt,
            "session_id": f"pi_trade_{task.id}_{now.strftime('%Y%m%d')}",
            "mode": "trade"  # 使用交易模式（全工具+交易提示词）
        }).encode("utf-8")

        pi_url = get_settings().PI_SERVER_URL
        req = urllib.request.Request(
            pi_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        ctx = ssl.create_default_context()

        logger.info(f"[{execution_id}] Sending pi_trade request for {task.id}...")
        with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            reply = data.get("reply", "")
            elapsed = data.get("elapsed_ms", 0)
            logger.info(f"[{execution_id}] Pi trade response ({len(reply)} chars, {elapsed}ms)")

        if not reply or reply == '(无回复)':
            raise RuntimeError("Pi 未返回有效交易报告")

        # 解析策略信号并写入策略链
        signal_match = re.search(
            r'SIGNAL:\s*(green|yellow|red)\s+POSITION:\s*(\d+)\s*REASON:\s*(.+)',
            reply, re.IGNORECASE
        )
        stance = 'yellow'
        position_limit = 60
        reason_str = ''
        if signal_match:
            try:
                stance = signal_match.group(1).lower()
                position_limit = int(signal_match.group(2))
                reason_str = signal_match.group(3).strip()
                from core.utils.strategy_chain import StrategyChain
                chain = StrategyChain()
                chain.set_pi_confirmation(
                    stance=stance,
                    position_limit=position_limit,
                    reason=reason_str,
                )
                logger.info(f"[{execution_id}] Pi signal: {signal_match.group(1)} limit={signal_match.group(2)}%")

                # ── 仓位利用率校验 ──
                # 如果 Pi 建议仓位 > 实际仓位的 3 倍（利用率 < 33%），记录警告
                try:
                    from core.utils.strategy_chain import StrategyChain
                    chain = StrategyChain()
                    # 获取账户实际仓位
                    import urllib.request as _ur, json as _js, ssl as _ssl
                    _ctx = _ssl.create_default_context()
                    portfolio_url = f"{get_settings().MARCUS_API_URL}/portfolio" if hasattr(get_settings(), 'MARCUS_API_URL') else "http://localhost:8000/api/v1/portfolio"
                    try:
                        _req = _ur.request.Request(portfolio_url, headers={"Accept": "application/json"})
                        with _ur.request.urlopen(_req, context=_ctx, timeout=10) as _resp:
                            _pdata = _js.loads(_resp.read().decode("utf-8"))
                            _acc = _pdata.get('account', {})
                            _total_asset = _acc.get('total_asset', 100000)
                            _position_value = _acc.get('position_value', 0)
                            actual_pct = (_position_value / _total_asset * 100) if _total_asset > 0 else 0
                    except Exception:
                        actual_pct = 0
                    
                    if position_limit > 0 and actual_pct < position_limit * 0.3 and position_limit >= 20:
                        utilization = actual_pct / position_limit * 100
                        logger.warning(
                            f"[{execution_id}] [position_utilization] Pi建议{position_limit}% "
                            f"但实际仅{actual_pct:.1f}%（利用率{utilization:.0f}%），仓位严重脱节"
                        )
                        # 注入到下一轮提示中
                        chain.set_pi_confirmation(
                            stance=stance,
                            position_limit=position_limit,
                            reason=f"{reason_str} | ⚠️ 仓位利用率仅{utilization:.0f}%",
                        )
                except Exception as e:
                    logger.debug(f"[{execution_id}] Position utilization check skipped: {e}")
            except Exception as e:
                logger.error(f"[{execution_id}] Failed to write Pi signal: {e}")

        # === 持久化交易报告到 memory/trade-reports/ ===
        self._save_trade_report(task.id, execution_id, reply, stance, position_limit, reason_str)

        # 返回完整报告（含 SIGNAL 行供后续通知使用）
        return reply

    def _execute_pi_reflect(self, task: TaskConfig, execution_id: str) -> str:
        """
        Pi 周度反思模式 —— 使用 DeepSeek-v4-pro + 最高思考等级。

        流程：
        1. 计算本周一和本周五日期
        2. 构造反思指令：告知 Pi 查询整周 Pi 分析历史
        3. 发送给 Pi Server /chat（mode=reflect）
        4. Pi 调用 get_pi_analysis_history → 深度分析
        5. 输出周度反思报告
        6. 提取 SIGNAL 写入策略链
        """
        import urllib.request, json as _json, ssl, re
        from datetime import timedelta

        now = datetime.now()
        # 本周一
        monday = now - timedelta(days=now.weekday())
        start_date = monday.strftime('%Y-%m-%d')
        end_date = now.strftime('%Y-%m-%d')

        prompt = (
            f"现在是 {now.strftime('%Y-%m-%d %H:%M:%S')}（周五收盘后），"
            f"请执行周度反思。\n\n"
            f"本周日期范围：{start_date} → {end_date}\n\n"
            f"请立即执行以下操作：\n"
            f"1. 调用 get_pi_analysis_history(start_date=\"{start_date}\", end_date=\"{end_date}\")"
            f" 获取整周全部 Pi 分析记录\n"
            f"2. 调用 get_trade_history(start_date=\"{start_date}\", end_date=\"{end_date}\")"
            f" 获取整周全部交易执行报告（含买卖决策、仓位变化、组合逻辑）\n"
            f"3. 调用 get_latest_scan_report() 了解周五收盘市场状态\n"
            f"4. 调用 get_portfolio() 了解最终账户状况\n"
            f"5. 调用 get_market_indices() 看本周大盘涨跌\n"
            f"6. 按反思 SOP 逐日分析 Pi 立场演变，同时对比交易报告评估策略执行质量\n"
            f"7. 输出完整的周度反思报告\n\n"
            f"⚠️ 注意：本周 Pi 扫描系统可能未完全运行，数据可能稀疏甚至为空。"
            f"但你仍然必须产出一份有价值的反思报告——即使只有一天数据也要深度分析，"
            f"无数据时则基于持仓和交易记录评估本周表现。不要因数据不足而拒绝输出。\n\n"
            f"请用 DeepSeek-v4-pro 最高思考模式进行深度推理，不要匆忙下结论。\n"
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        payload = _json.dumps({
            "message": prompt,
            "session_id": f"pi_reflect_{task.id}_{now.strftime('%Y%m%d')}",
            "mode": "reflect"  # 反思模式（隔离的工具集+提示词+模型+最高思考）
        }).encode("utf-8")

        pi_url = get_settings().PI_SERVER_URL
        req = urllib.request.Request(
            pi_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        ctx = ssl.create_default_context()

        logger.info(f"[{execution_id}] Sending pi_reflect request for {task.id} "
                    f"(日期范围: {start_date}→{end_date})...")
        # 反思可能需要更长时间（专家组群聊：数据采集 + 4 专家并行 + 交叉评论 + 主持人综合）
        with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            reply = data.get("reply", "")
            elapsed = data.get("elapsed_ms", 0)
            logger.info(f"[{execution_id}] Pi reflect response ({len(reply)} chars, {elapsed}ms)")

        if not reply or reply == '(无回复)':
            raise RuntimeError("Pi 未返回有效周度反思报告")

        # 解析策略信号并写入策略链
        stance = 'yellow'
        position_limit = 60
        reason_str = ''
        signal_match = re.search(
            r'SIGNAL:\s*(green|yellow|red)\s+POSITION:\s*(\d+)\s*REASON:\s*(.+)',
            reply, re.IGNORECASE
        )
        if signal_match:
            try:
                stance = signal_match.group(1).lower()
                position_limit = int(signal_match.group(2))
                reason_str = signal_match.group(3).strip()
                from core.utils.strategy_chain import StrategyChain
                chain = StrategyChain()
                chain.set_pi_confirmation(
                    stance=stance,
                    position_limit=position_limit,
                    reason=reason_str,
                )
                logger.info(f"[{execution_id}] Pi reflect signal: {stance} "
                            f"limit={position_limit}%")
            except Exception as e:
                logger.error(f"[{execution_id}] Failed to write Pi reflect signal: {e}")

        # === 持久化周度反思报告 ===
        self._save_weekly_reflect(start_date, end_date, reply, stance, position_limit, reason_str)

        return reply

    def _on_job_executed(self, event):
        """任务执行成功回调"""
        logger.debug(f"Job executed: {event.job_id}")

    def _on_job_error(self, event):
        """任务执行失败回调"""
        logger.error(f"Job error: {event.job_id}, exception: {event.exception}")

    def _on_job_missed(self, event):
        """任务错过的回调"""
        logger.warning(f"Job missed: {event.job_id}")

    # ==================== Public API ====================

    def get_tasks(self) -> List[Dict]:
        """获取所有任务"""
        result = []
        for task_id, task in self.tasks.items():
            # Get next run time
            job = self.scheduler.get_job(task_id)
            next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

            # Get last execution
            last_exec = self._get_last_execution(task_id)

            result.append({
                'id': task.id,
                'name': task.name,
                'description': task.description,
                'enabled': task.enabled,
                'type': task.type,
                'schedule': task.schedule,
                'notifications': task.notifications,
                'next_run': next_run,
                'last_execution': {
                    'status': last_exec.status if last_exec else None,
                    'started_at': last_exec.started_at.isoformat() if last_exec else None,
                    'finished_at': last_exec.finished_at.isoformat() if last_exec and last_exec.finished_at else None,
                } if last_exec else None,
            })
        return result

    def get_task(self, task_id: str) -> Optional[Dict]:
        """获取单个任务"""
        task = self.tasks.get(task_id)
        if not task:
            return None

        job = self.scheduler.get_job(task_id)
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

        return {
            'id': task.id,
            'name': task.name,
            'description': task.description,
            'enabled': task.enabled,
            'type': task.type,
            'schedule': task.schedule,
            'script': task.script,
            'output': task.output,
            'notifications': task.notifications,
            'next_run': next_run,
        }

    def get_task_executions(self, task_id: str, limit: int = 20) -> List[Dict]:
        """获取任务执行历史"""
        executions = [
            e for e in self.executions.values()
            if e.task_id == task_id
        ]
        # Sort by started_at descending
        executions.sort(key=lambda x: x.started_at, reverse=True)
        return [
            {
                'id': e.id,
                'task_id': e.task_id,
                'task_name': e.task_name,
                'status': e.status,
                'started_at': e.started_at.isoformat(),
                'finished_at': e.finished_at.isoformat() if e.finished_at else None,
                'output': e.output[:500] if e.output else "",
                'error': e.error[:200] if e.error else "",
                'return_code': e.return_code,
            }
            for e in executions[:limit]
        ]

    def get_execution_log(self, execution_id: str) -> Optional[str]:
        """获取执行详细日志文件路径"""
        for task_id_dir in (self._get_workspace_path() / "logs").iterdir():
            if task_id_dir.is_dir():
                log_file = task_id_dir / f"{execution_id}.json"
                if log_file.exists():
                    return str(log_file)
        return None

    def _get_last_execution(self, task_id: str) -> Optional[JobExecution]:
        """获取任务最后执行记录"""
        task_execs = [e for e in self.executions.values() if e.task_id == task_id]
        if not task_execs:
            return None
        return max(task_execs, key=lambda x: x.started_at)

    def trigger_task(self, task_id: str) -> Dict:
        """手动触发任务"""
        task = self.tasks.get(task_id)
        if not task:
            return {'success': False, 'error': 'Task not found'}

        if not task.enabled:
            return {'success': False, 'error': 'Task is disabled'}

        # Run in background thread，手动触发跳过时间窗口校验
        thread = threading.Thread(target=self._execute_task, args=(task_id, True))
        thread.start()

        return {'success': True, 'message': f'Task {task.name} triggered'}

    def enable_task(self, task_id: str) -> Dict:
        """启用任务"""
        task = self.tasks.get(task_id)
        if not task:
            return {'success': False, 'error': 'Task not found'}

        task.enabled = True
        self._add_job(task)
        logger.info(f"Task {task_id} enabled")

        return {'success': True, 'message': f'Task {task.name} enabled'}

    def disable_task(self, task_id: str) -> Dict:
        """禁用任务"""
        task = self.tasks.get(task_id)
        if not task:
            return {'success': False, 'error': 'Task not found'}

        task.enabled = False
        self.scheduler.remove_job(task_id)
        logger.info(f"Task {task_id} disabled")

        return {'success': True, 'message': f'Task {task.name} disabled'}

    def update_task(self, task_id: str, updates: Dict) -> Dict:
        """更新任务配置"""
        task = self.tasks.get(task_id)
        if not task:
            return {'success': False, 'error': 'Task not found'}

        # Validate cron expression if schedule is being updated
        if 'schedule' in updates:
            schedule = updates['schedule']
            expr = schedule.get('expr', '')
            if expr:
                valid, error_msg = self._validate_cron(expr)
                if not valid:
                    return {'success': False, 'error': error_msg}

        # Update fields
        if 'enabled' in updates:
            task.enabled = updates['enabled']
        if 'schedule' in updates:
            task.schedule = updates['schedule']
        if 'notifications' in updates:
            task.notifications = updates['notifications']

        # Remove old job and add new one
        try:
            self.scheduler.remove_job(task_id)
        except:
            pass

        if task.enabled:
            self._add_job(task)

        # Persist changes to YAML config file
        self._persist_config()

        logger.info(f"Task {task_id} updated")

        return {'success': True, 'message': f'Task {task.name} updated'}

    def _validate_cron(self, expr: str) -> tuple:
        """Validate a cron expression.
        Returns (is_valid, error_message)
        """
        try:
            parts = expr.strip().split()
            if len(parts) != 5:
                return False, "Cron expression must have exactly 5 fields: minute hour day month day_of_week"

            minute, hour, day, month, day_of_week = parts

            # Validate each field by trying to create a CronTrigger
            CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
            )
            return True, ""
        except Exception as e:
            return False, f"Invalid cron expression: {str(e)}"

    def _persist_config(self):
        """Save current task configurations back to the YAML config file"""
        try:
            # Build the config structure matching the original format
            tasks_list = []
            for task_id, task in self.tasks.items():
                task_dict = {
                    'id': task.id,
                    'name': task.name,
                    'description': task.description,
                    'enabled': task.enabled,
                    'type': task.type,
                    'schedule': task.schedule,
                    'script': task.script,
                    'output': task.output,
                    'notifications': task.notifications,
                    'depends_on': task.depends_on,
                }
                if task.type == 'pi_trade':
                    task_dict['pi_prompt'] = task.pi_prompt
                tasks_list.append(task_dict)

            config = {
                'settings': self.settings,
                'tasks': tasks_list,
            }

            # Write back to config file
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(
                    config,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    indent=2,
                )

            logger.info(f"Configuration saved to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to persist config: {e}")
            raise

    def get_next_runs(self) -> List[Dict]:
        """获取即将执行的任务"""
        jobs = self.scheduler.get_jobs()
        result = []
        for job in jobs:
            if job.next_run_time:
                result.append({
                    'task_id': job.id,
                    'task_name': job.name,
                    'next_run': job.next_run_time.isoformat(),
                    'seconds_until': (job.next_run_time - datetime.now().astimezone()).total_seconds(),
                })

        result.sort(key=lambda x: x['seconds_until'])
        return result[:10]

    def get_scheduler_status(self) -> Dict:
        """获取调度器状态"""
        return {
            'running': self.scheduler.running,
            'task_count': len(self.tasks),
            'enabled_count': sum(1 for t in self.tasks.values() if t.enabled),
            'jobs_count': len(self.scheduler.get_jobs()),
            'executions_today': sum(
                1 for e in self.executions.values()
                if e.started_at.date() == datetime.now().date()
            ),
        }


# Global instance
scheduler_service = SchedulerService()
