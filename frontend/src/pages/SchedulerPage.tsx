import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Play, Pause, RotateCcw, Clock, CheckCircle, XCircle, AlertCircle, Loader2, FileText, ChevronDown, ChevronUp, Settings, Bell, Brain, MessageSquare } from 'lucide-react';
import { schedulerApi } from '../api/client';
import CronEditor from '../components/CronEditor';
import type { AxiosError } from 'axios';

interface Task {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  type: string;  // "script" | "pi_trade"
  schedule: {
    type: string;
    expr: string;
    timezone: string;
  };
  notifications?: {
    on_success: boolean;
    on_failure: boolean;
    pi_analysis?: boolean;
    channels?: string[];
  };
  next_run: string | null;
  last_execution: {
    status: string | null;
    started_at: string | null;
    finished_at: string | null;
  } | null;
}

interface SchedulerStatus {
  running: boolean;
  task_count: number;
  enabled_count: number;
  jobs_count: number;
  executions_today: number;
}

interface NextRun {
  task_id: string;
  task_name: string;
  next_run: string;
  seconds_until: number;
}

interface Execution {
  id: string;
  task_id: string;
  task_name: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  output: string;
  error: string;
  return_code: number | null;
}

export default function SchedulerPage() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [nextRuns, setNextRuns] = useState<NextRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [expandedExec, setExpandedExec] = useState<string | null>(null);
  const [execLog, setExecLog] = useState<{ [execId: string]: string }>({});
  const [loadingLog, setLoadingLog] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [editingSchedule, setEditingSchedule] = useState<{ type: string; expr: string; timezone: string } | null>(null);
  const [editingNotifications, setEditingNotifications] = useState<{ on_success: boolean; on_failure: boolean; pi_analysis: boolean; channels: string[] }>({ on_success: true, on_failure: true, pi_analysis: false, channels: ['qqbot'] });
  const [savingEdit, setSavingEdit] = useState(false);

  const fetchData = async () => {
    try {
      const [statusRes, tasksRes, nextRunsRes] = await Promise.all([
        schedulerApi.getStatus(),
        schedulerApi.getTasks(),
        schedulerApi.getNextRuns(),
      ]);
      setStatus(statusRes.data);
      setTasks(tasksRes.data.tasks || []);
      setNextRuns(nextRunsRes.data.runs || []);
      setError(null);
    } catch (err) {
      const axiosError = err as AxiosError;
      setError(axiosError.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleTask = async (task: Task) => {
    setActionLoading(task.id);
    try {
      if (task.enabled) {
        await schedulerApi.disableTask(task.id);
      } else {
        await schedulerApi.enableTask(task.id);
      }
      await fetchData();
    } catch (err) {
      const axiosError = err as AxiosError;
      setError(axiosError.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleTriggerTask = async (taskId: string) => {
    setActionLoading(taskId);
    try {
      await schedulerApi.triggerTask(taskId);
      await fetchData();
    } catch (err) {
      const axiosError = err as AxiosError;
      setError(axiosError.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReload = async () => {
    setActionLoading('reload');
    try {
      await schedulerApi.reload();
      await fetchData();
    } catch (err) {
      const axiosError = err as AxiosError;
      setError(axiosError.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleToggleScheduler = async () => {
    setActionLoading('toggle');
    try {
      if (status?.running) {
        await schedulerApi.stop();
      } else {
        await schedulerApi.start();
      }
      await fetchData();
    } catch (err) {
      const axiosError = err as AxiosError;
      setError(axiosError.message);
    } finally {
      setActionLoading(null);
    }
  };

  const handleViewDetails = async (task: Task) => {
    setSelectedTask(task);
    setExpandedExec(null);
    try {
      const res = await schedulerApi.getTaskExecutions(task.id, 20);
      setExecutions(res.data.executions || []);
    } catch {
      setExecutions([]);
    }
  };

  const handleViewLog = async (execId: string) => {
    if (expandedExec === execId) {
      setExpandedExec(null);
      return;
    }
    setExpandedExec(execId);
    if (!execLog[execId]) {
      setLoadingLog(execId);
      try {
        const res = await schedulerApi.getExecutionLog(execId);
        setExecLog(prev => ({ ...prev, [execId]: res.data.content || '' }));
      } catch {
        setExecLog(prev => ({ ...prev, [execId]: 'Failed to load log' }));
      } finally {
        setLoadingLog(null);
      }
    }
  };

  const handleEditTask = (task: Task) => {
    setEditingTask(task);
    setEditingSchedule({ ...task.schedule });
    setEditingNotifications({
      on_success: task.notifications?.on_success ?? true,
      on_failure: task.notifications?.on_failure ?? true,
      pi_analysis: task.notifications?.pi_analysis ?? false,
      channels: task.notifications?.channels ?? ['qqbot'],
    });
  };

  const handleSaveEdit = async () => {
    if (!editingTask || !editingSchedule) return;
    setSavingEdit(true);
    try {
      await schedulerApi.updateTask(editingTask.id, {
        schedule: editingSchedule,
        enabled: editingTask.enabled,
        notifications: editingNotifications,
      });
      setEditingTask(null);
      setEditingSchedule(null);
      await fetchData();
    } catch (err) {
      const axiosError = err as AxiosError & { response?: { data?: { detail?: string } } };
      const msg = axiosError.response?.data?.detail || axiosError.message;
      setError(msg);
    } finally {
      setSavingEdit(false);
    }
  };

  const getStatusIcon = (status: string | null) => {
    switch (status) {
      case 'success':
        return <CheckCircle size={16} className="text-green-400" />;
      case 'failed':
        return <XCircle size={16} className="text-red-400" />;
      case 'running':
        return <Loader2 size={16} className="text-blue-400 animate-spin" />;
      default:
        return <AlertCircle size={16} className="text-gray-400" />;
    }
  };

  const formatTime = (iso: string | null) => {
    if (!iso) return '-';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const formatCron = (expr: string) => {
    const parts = expr.split(' ');
    if (parts.length !== 5) return expr;
    const [minute, hour, , , dow] = parts;

    const timeDesc = (() => {
      const hPart = hour === '*' ? '每时' : `${hour}时`;
      let mPart: string;
      if (minute === '*') mPart = '每分';
      else if (minute.includes('/')) mPart = `每${minute.split('/')[1]}分`;
      else if (minute.includes(',')) mPart = `${minute}分`;
      else mPart = `${minute}分`;
      return `${hPart} ${mPart}`;
    })();

    const dowPart = (() => {
      if (dow === '*') return '每天';
      if (dow === '1-5') return '工作日';
      if (dow === '0,6' || dow === '6,0') return '周末';
      if (dow === '0' || dow === '7') return '周日';
      if (dow === '1') return '周一';
      if (dow === '2') return '周二';
      if (dow === '3') return '周三';
      if (dow === '4') return '周四';
      if (dow === '5') return '周五';
      if (dow === '6') return '周六';
      return `周${dow}`;
    })();

    return `${timeDesc} · ${dowPart}`;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 size={32} className="animate-spin text-primary-400" />
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto bg-dark-300/30">
      {/* ====== Top Header ====== */}
      <div className="sticky top-0 z-10 backdrop-blur-md bg-dark-300/80 border-b border-gray-800/60 px-8 py-4">
        <div className="max-w-[1920px] mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-2.5 h-2.5 rounded-full animate-pulse ${status?.running ? 'bg-green-400 shadow-[0_0_8px_rgba(74,222,128,0.5)]' : 'bg-red-400 shadow-[0_0_8px_rgba(248,113,113,0.5)]'}`} />
            <h1 className="text-xl font-bold tracking-tight">{t('scheduler.title')}</h1>
            <span className="text-xs text-gray-500 ml-1">v1.0</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-xs text-gray-500 mr-2">
              {status?.running ? '🟢 运行中' : '🔴 已停止'} · {status?.task_count || 0} 个任务
            </div>
            <button
              onClick={handleToggleScheduler}
              disabled={actionLoading === 'toggle'}
              className={`px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-medium transition-all duration-200 ${
                status?.running
                  ? 'bg-red-500/20 border border-red-500/30 text-red-400 hover:bg-red-500/30 hover:border-red-500/50'
                  : 'bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/30 hover:border-emerald-500/50'
              } disabled:opacity-50`}
            >
              {status?.running ? <Pause size={15} /> : <Play size={15} />}
              {status?.running ? t('scheduler.paused') : t('scheduler.enabled')}
            </button>
            <button
              onClick={handleReload}
              disabled={actionLoading === 'reload'}
              className="px-4 py-2 rounded-lg flex items-center gap-2 text-sm text-gray-300 bg-gray-700/50 border border-gray-600/50 hover:bg-gray-600/50 transition-all duration-200 disabled:opacity-50"
            >
              <RotateCcw size={15} className={actionLoading === 'reload' ? 'animate-spin' : ''} />
              {t('common.refresh')}
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-[1920px] mx-auto p-8 space-y-6">
        {/* ====== Status Cards ====== */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatusCard
            label={t('scheduler.status')}
            value={status?.running ? t('scheduler.running') : t('scheduler.disabled')}
            accent={status?.running ? 'emerald' : 'red'}
            icon={status?.running ? <Play size={18} /> : <Pause size={18} />}
          />
          <StatusCard
            label={t('scheduler.tasks')}
            value={`${status?.task_count || 0}`}
            sub={status ? `${status.enabled_count}/${status.task_count} enabled` : ''}
            accent="blue"
            icon={<Clock size={18} />}
          />
          <StatusCard
            label={t('scheduler.executions_today', { count: status?.executions_today || 0 })}
            value={`${status?.executions_today || 0}`}
            accent="amber"
            icon={<CheckCircle size={18} />}
          />
          <StatusCard
            label="Scheduler Jobs"
            value={`${status?.jobs_count || 0}`}
            accent="violet"
            icon={<Settings size={18} />}
          />
        </div>

        {/* ====== Main Content: Sidebar + Task List ====== */}
        <div className="flex flex-col xl:flex-row gap-6">
          {/* ---- Left Sidebar: Next Runs ---- */}
          <div className="xl:w-80 flex-shrink-0 space-y-4">
            {nextRuns.length > 0 && (
              <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 overflow-hidden backdrop-blur-sm">
                <div className="px-5 py-3.5 border-b border-gray-800/60 flex items-center gap-2.5">
                  <div className="p-1.5 rounded-lg bg-blue-500/10">
                    <Clock size={16} className="text-blue-400" />
                  </div>
                  <h2 className="text-sm font-semibold text-white">Upcoming Runs</h2>
                  <span className="ml-auto text-xs text-gray-500">{nextRuns.length} tasks</span>
                </div>
                <div className="p-3 space-y-2">
                  {nextRuns.slice(0, 6).map((run, idx) => (
                    <div
                      key={run.task_id}
                      className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-dark-100/50 hover:bg-dark-100 transition-colors group"
                    >
                      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                        idx === 0 ? 'bg-blue-400 shadow-[0_0_6px_rgba(96,165,250,0.5)]' : 'bg-gray-600'
                      }`} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-white truncate">{run.task_name}</div>
                        <div className="text-xs text-gray-500 mt-0.5">
                          {formatTime(run.next_run)}
                        </div>
                      </div>
                      <div className="text-xs font-mono text-gray-400 bg-dark-300/80 px-2 py-0.5 rounded group-hover:text-gray-300 transition-colors">
                        {Math.round(run.seconds_until / 60)}min
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Quick Stats */}
            <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 p-5 backdrop-blur-sm">
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Quick Actions</h3>
              <div className="space-y-2">
                <button
                  onClick={handleReload}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-400 hover:text-white hover:bg-dark-100 transition-colors"
                >
                  <RotateCcw size={14} />
                  Reload Configuration
                </button>
                <div className="text-xs text-gray-600 px-3 py-1">
                  Auto-refresh every 30s
                </div>
              </div>
            </div>
          </div>

          {/* ---- Main Area: Task Cards ---- */}
          <div className="flex-1 min-w-0">
            <div className="bg-dark-200/80 rounded-xl border border-gray-800/60 overflow-hidden backdrop-blur-sm">
              <div className="px-6 py-4 border-b border-gray-800/60 flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <div className="p-1.5 rounded-lg bg-amber-500/10">
                    <Settings size={16} className="text-amber-400" />
                  </div>
                  <h2 className="text-sm font-semibold text-white">Task List</h2>
                </div>
                <span className="text-xs text-gray-500">{tasks.length} tasks</span>
              </div>

              <div className="p-4">
                {tasks.length === 0 ? (
                  <div className="py-16 text-center text-gray-500">
                    <Clock size={40} className="mx-auto mb-3 opacity-30" />
                    {t('common.noData')}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {tasks.map((task) => (
                      <div
                        key={task.id}
                        className="group rounded-lg bg-dark-100/40 border border-gray-800/40 hover:border-gray-700/60 hover:bg-dark-100/70 transition-all duration-200"
                      >
                        <div className="px-5 py-4">
                          <div className="flex items-start justify-between gap-4">
                            {/* Left: Task Info */}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2.5 mb-2">
                                <span className={`relative flex h-2.5 w-2.5 ${task.enabled ? '' : 'opacity-40'}`}>
                                  <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${
                                    task.enabled ? 'bg-green-400 animate-ping' : 'bg-gray-500'
                                  }`} />
                                  <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                                    task.enabled ? 'bg-green-400' : 'bg-gray-500'
                                  }`} />
                                </span>
                                <span className="font-semibold text-sm text-white">{task.name}</span>
                                {task.type === 'pi_trade' && (
                                  <span className="px-1.5 py-0.5 text-[10px] rounded font-medium bg-purple-500/15 text-purple-400 border border-purple-500/25" title="Pi Agent 自主交易模式">
                                    🤖 Pi
                                  </span>
                                )}
                                {task.type === 'script' && (
                                  <span className="px-1.5 py-0.5 text-[10px] rounded font-medium bg-gray-500/15 text-gray-400 border border-gray-500/25" title="固定脚本模式">
                                    📜 Script
                                  </span>
                                )}
                                {task.description && (
                                  <span className="text-xs text-gray-500 hidden sm:inline">— {task.description}</span>
                                )}
                              </div>
                              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-gray-500">
                                <span className="flex items-center gap-1">
                                  <Clock size={11} />
                                  {formatCron(task.schedule.expr)}
                                </span>
                                <span className="text-gray-600">|</span>
                                <span>Next: {task.next_run ? formatTime(task.next_run) : '-'}</span>
                                {task.last_execution?.status && (
                                  <>
                                    <span className="text-gray-600">|</span>
                                    <span className={`flex items-center gap-1 ${
                                      task.last_execution.status === 'success' ? 'text-green-500' :
                                      task.last_execution.status === 'failed' ? 'text-red-500' : 'text-gray-400'
                                    }`}>
                                      {getStatusIcon(task.last_execution.status)}
                                      Last: {task.last_execution.status}
                                    </span>
                                  </>
                                )}
                              </div>
                            </div>

                            {/* Right: Actions */}
                            <div className="flex items-center gap-1.5 flex-shrink-0">
                              <button
                                onClick={() => handleEditTask(task)}
                                className="px-3 py-1.5 text-xs rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-400 hover:bg-amber-500/25 hover:text-amber-300 transition-all flex items-center gap-1"
                              >
                                <Settings size={12} />
                                Edit
                              </button>
                              <button
                                onClick={() => handleViewDetails(task)}
                                className="px-3 py-1.5 text-xs rounded-lg bg-gray-500/10 border border-gray-600/30 text-gray-400 hover:bg-gray-500/20 hover:text-white transition-all"
                              >
                                Logs
                              </button>
                              <button
                                onClick={() => handleTriggerTask(task.id)}
                                disabled={actionLoading === task.id || !task.enabled}
                                className="px-3 py-1.5 text-xs rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-400 hover:bg-blue-500/25 hover:text-blue-300 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                              >
                                Run
                              </button>
                              <button
                                onClick={() => handleToggleTask(task)}
                                disabled={actionLoading === task.id}
                                className={`px-3 py-1.5 text-xs rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 ${
                                  task.enabled
                                    ? 'bg-red-500/15 border border-red-500/30 text-red-400 hover:bg-red-500/25 hover:text-red-300'
                                    : 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/25 hover:text-emerald-300'
                                }`}
                              >
                                {task.enabled ? <Pause size={11} /> : <Play size={11} />}
                                {task.enabled ? 'Disable' : 'Enable'}
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ====== Task Details Modal ====== */}
      {selectedTask && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50" onClick={() => setSelectedTask(null)}>
          <div className="bg-dark-200 rounded-xl border border-gray-700/60 w-full max-w-3xl max-h-[80vh] overflow-hidden shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-1.5 rounded-lg bg-blue-500/10">
                  <FileText size={16} className="text-blue-400" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold">{selectedTask.name}</h2>
                  <div className="text-xs text-gray-500">Execution History</div>
                </div>
              </div>
              <button onClick={() => setSelectedTask(null)} className="p-1.5 rounded-lg hover:bg-gray-700/50 text-gray-400 hover:text-white transition-colors">
                <XCircle size={18} />
              </button>
            </div>
            <div className="p-6 overflow-auto max-h-[65vh]">
              {executions.length === 0 ? (
                <div className="py-12 text-center text-gray-500">
                  <Clock size={36} className="mx-auto mb-3 opacity-30" />
                  {t('common.noData')}
                </div>
              ) : (
                <div className="space-y-2">
                  {executions.map((exec) => (
                    <div key={exec.id} className="rounded-lg bg-dark-100/50 border border-gray-800/40 overflow-hidden">
                      <div className="px-4 py-3 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div className={`p-1 rounded ${
                            exec.status === 'success' ? 'bg-green-500/10' :
                            exec.status === 'failed' ? 'bg-red-500/10' :
                            exec.status === 'running' ? 'bg-blue-500/10' : 'bg-gray-500/10'
                          }`}>
                            {getStatusIcon(exec.status)}
                          </div>
                          <div>
                            <span className={`text-sm font-medium capitalize ${
                              exec.status === 'success' ? 'text-green-400' :
                              exec.status === 'failed' ? 'text-red-400' :
                              exec.status === 'running' ? 'text-blue-400' : 'text-gray-400'
                            }`}>{exec.status}</span>
                            <span className="text-xs text-gray-500 ml-2">
                              {exec.finished_at ? `${((new Date(exec.finished_at).getTime() - new Date(exec.started_at).getTime()) / 1000).toFixed(1)}s` : 'running...'}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-gray-500">{formatTime(exec.started_at)}</span>
                          <button
                            onClick={() => handleViewLog(exec.id)}
                            className="px-2.5 py-1 text-xs rounded-lg bg-gray-500/10 border border-gray-600/30 text-gray-400 hover:bg-gray-500/20 hover:text-white transition-all flex items-center gap-1"
                          >
                            <FileText size={11} />
                            {expandedExec === exec.id ? 'Hide' : 'Log'}
                            {expandedExec === exec.id ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                          </button>
                        </div>
                      </div>
                      {exec.error && (
                        <div className="mx-4 mb-3 text-xs text-red-400 p-2.5 bg-red-500/5 border border-red-500/10 rounded-lg">
                          {exec.error}
                        </div>
                      )}
                      {expandedExec === exec.id && (
                        <div className="border-t border-gray-800/40 px-4 py-3">
                          {loadingLog === exec.id ? (
                            <div className="flex items-center gap-2 text-gray-400 text-xs">
                              <Loader2 size={12} className="animate-spin" />
                              Loading...
                            </div>
                          ) : (
                            <pre className="text-xs text-gray-300 bg-dark-300/80 p-3 rounded-lg max-h-80 overflow-auto whitespace-pre-wrap break-all font-mono leading-relaxed">
                              {execLog[exec.id] || exec.output || 'No output'}
                            </pre>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ====== Edit Modal ====== */}
      {editingTask && editingSchedule && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50" onClick={() => { setEditingTask(null); setEditingSchedule(null); }}>
          <div className="bg-dark-200 rounded-xl border border-gray-700/60 w-full max-w-xl max-h-[85vh] overflow-hidden shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-1.5 rounded-lg bg-amber-500/10">
                  <Clock size={16} className="text-amber-400" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold">{editingTask.name}</h2>
                  <div className="text-xs text-gray-500">Edit Cron Schedule</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleSaveEdit}
                  disabled={savingEdit}
                  className="px-4 py-1.5 text-xs rounded-lg bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/30 transition-all disabled:opacity-50 flex items-center gap-1.5"
                >
                  {savingEdit && <Loader2 size={12} className="animate-spin" />}
                  Save Changes
                </button>
                <button
                  onClick={() => { setEditingTask(null); setEditingSchedule(null); }}
                  className="p-1.5 rounded-lg hover:bg-gray-700/50 text-gray-400 hover:text-white transition-colors"
                >
                  <XCircle size={18} />
                </button>
              </div>
            </div>
            <div className="p-6 overflow-auto max-h-[65vh] space-y-6">
              <CronEditor
                value={editingSchedule}
                onChange={(schedule) => setEditingSchedule(schedule)}
              />

              {/* ===== Notifications Settings ===== */}
              <div className="border-t border-gray-800 pt-5">
                <div className="flex items-center gap-2 mb-4">
                  <Bell size={15} className="text-indigo-400" />
                  <h3 className="text-sm font-semibold">Notifications</h3>
                </div>

                <div className="space-y-3">
                  {/* QQ Bot Channel */}
                  <label className="flex items-center gap-3 cursor-pointer group">
                    <div className={`w-9 h-5 rounded-full relative transition-colors ${editingNotifications.channels.includes('qqbot') ? 'bg-indigo-500' : 'bg-gray-700'}`}
                      onClick={() => {
                        const ch = editingNotifications.channels.includes('qqbot')
                          ? editingNotifications.channels.filter(c => c !== 'qqbot')
                          : [...editingNotifications.channels, 'qqbot'];
                        setEditingNotifications({ ...editingNotifications, channels: ch });
                      }}
                    >
                      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${editingNotifications.channels.includes('qqbot') ? 'left-[18px]' : 'left-[2px]'}`} />
                    </div>
                    <div>
                      <div className="flex items-center gap-1.5 text-xs font-medium text-gray-300">
                        <MessageSquare size={12} /> QQ Bot
                      </div>
                      <div className="text-[11px] text-gray-500">Push notifications via QQ</div>
                    </div>
                  </label>

                  {/* On Success */}
                  <label className="flex items-center gap-3 cursor-pointer">
                    <div className={`w-9 h-5 rounded-full relative transition-colors ${editingNotifications.on_success ? 'bg-emerald-500' : 'bg-gray-700'}`}
                      onClick={() => setEditingNotifications({ ...editingNotifications, on_success: !editingNotifications.on_success })}
                    >
                      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${editingNotifications.on_success ? 'left-[18px]' : 'left-[2px]'}`} />
                    </div>
                    <div>
                      <span className="text-xs text-gray-300">Notify on success</span>
                    </div>
                  </label>

                  {/* On Failure */}
                  <label className="flex items-center gap-3 cursor-pointer">
                    <div className={`w-9 h-5 rounded-full relative transition-colors ${editingNotifications.on_failure ? 'bg-red-500' : 'bg-gray-700'}`}
                      onClick={() => setEditingNotifications({ ...editingNotifications, on_failure: !editingNotifications.on_failure })}
                    >
                      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${editingNotifications.on_failure ? 'left-[18px]' : 'left-[2px]'}`} />
                    </div>
                    <div>
                      <span className="text-xs text-gray-300">Notify on failure</span>
                    </div>
                  </label>

                  {/* Pi Analysis — only when QQ Bot is enabled */}
                  {editingNotifications.channels.includes('qqbot') && (
                    <label className="flex items-center gap-3 cursor-pointer">
                      <div className={`w-9 h-5 rounded-full relative transition-colors ${editingNotifications.pi_analysis ? 'bg-amber-500' : 'bg-gray-700'}`}
                        onClick={() => setEditingNotifications({ ...editingNotifications, pi_analysis: !editingNotifications.pi_analysis })}
                      >
                        <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${editingNotifications.pi_analysis ? 'left-[18px]' : 'left-[2px]'}`} />
                      </div>
                      <div>
                        <div className="flex items-center gap-1.5 text-xs font-medium text-gray-300">
                          <Brain size={12} /> Pi Analysis
                        </div>
                        <div className="text-[11px] text-gray-500">AI analyzes output & generates report</div>
                      </div>
                    </label>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ====== Error Toast ====== */}
      {error && (
        <div className="fixed bottom-6 right-6 z-50 max-w-sm animate-in slide-in-from-bottom-2">
          <div className="flex items-start gap-3 bg-red-500/10 border border-red-500/30 rounded-xl p-4 backdrop-blur-md shadow-lg">
            <AlertCircle size={18} className="text-red-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="text-sm text-red-300">{error}</div>
            </div>
            <button onClick={() => setError(null)} className="p-1 rounded-lg hover:bg-red-500/20 text-red-400 hover:text-red-300 transition-colors">
              <XCircle size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ====== Helper Components ======

function StatusCard({ label, value, sub, accent, icon }: {
  label: string;
  value: string;
  sub?: string;
  accent: 'emerald' | 'red' | 'blue' | 'amber' | 'violet';
  icon: React.ReactNode;
}) {
  const colors = {
    emerald: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/20', glow: 'shadow-[inset_0_1px_0_rgba(52,211,153,0.1)]' },
    red:    { bg: 'bg-red-500/10',    text: 'text-red-400',    border: 'border-red-500/20',    glow: 'shadow-[inset_0_1px_0_rgba(248,113,113,0.1)]' },
    blue:   { bg: 'bg-blue-500/10',   text: 'text-blue-400',   border: 'border-blue-500/20',   glow: 'shadow-[inset_0_1px_0_rgba(96,165,250,0.1)]' },
    amber:  { bg: 'bg-amber-500/10',  text: 'text-amber-400',  border: 'border-amber-500/20',  glow: 'shadow-[inset_0_1px_0_rgba(251,191,36,0.1)]' },
    violet: { bg: 'bg-violet-500/10', text: 'text-violet-400', border: 'border-violet-500/20', glow: 'shadow-[inset_0_1px_0_rgba(167,139,250,0.1)]' },
  };
  const c = colors[accent];

  return (
    <div className={`rounded-xl border ${c.border} ${c.glow} p-5 backdrop-blur-sm bg-dark-200/60 hover:bg-dark-200/80 transition-all duration-300`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</span>
        <div className={`p-1.5 rounded-lg ${c.bg} ${c.text}`}>
          {icon}
        </div>
      </div>
      <div className={`text-2xl font-bold tracking-tight ${c.text}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  );
}