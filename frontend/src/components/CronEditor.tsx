import { useState, useEffect, useMemo, useCallback } from 'react';
import { Clock, Code, ChevronDown } from 'lucide-react';

interface CronEditorProps {
  value: { expr: string; timezone: string };
  onChange: (schedule: { type: string; expr: string; timezone: string }) => void;
}

interface CronField {
  minute: string;
  hour: string;
  day: string;
  month: string;
  dayOfWeek: string;
}

// Predefined common cron presets
const PRESETS: { label: string; expr: string; description: string }[] = [
  { label: '每分钟', expr: '* * * * *', description: 'Every minute' },
  { label: '每5分钟', expr: '*/5 * * * *', description: 'Every 5 minutes' },
  { label: '每10分钟', expr: '*/10 * * * *', description: 'Every 10 minutes' },
  { label: '每30分钟', expr: '*/30 * * * *', description: 'Every 30 minutes' },
  { label: '每小时', expr: '0 * * * *', description: 'Every hour at minute 0' },
  { label: '每天 8:00', expr: '0 8 * * *', description: 'Daily at 8:00 AM' },
  { label: '每天 9:00', expr: '0 9 * * *', description: 'Daily at 9:00 AM' },
  { label: '每天 13:00', expr: '0 13 * * *', description: 'Daily at 1:00 PM' },
  { label: '每天 15:00', expr: '0 15 * * *', description: 'Daily at 3:00 PM' },
  { label: '工作日 9:00', expr: '0 9 * * 1-5', description: 'Weekdays at 9:00 AM' },
  { label: '工作日 9:30', expr: '30 9 * * 1-5', description: 'Weekdays at 9:30 AM' },
  { label: '工作日 15:00', expr: '0 15 * * 1-5', description: 'Weekdays at 3:00 PM' },
  { label: '每周一 8:00', expr: '0 8 * * 1', description: 'Every Monday at 8:00 AM' },
  { label: '每周五 16:00', expr: '0 16 * * 5', description: 'Every Friday at 4:00 PM' },
];

const MINUTE_OPTIONS = [
  { value: '*', label: '每分钟' },
  { value: '0', label: '0 分（整点）' },
  { value: '5', label: '5 分' },
  { value: '10', label: '10 分' },
  { value: '15', label: '15 分' },
  { value: '20', label: '20 分' },
  { value: '25', label: '25 分' },
  { value: '30', label: '30 分（半点）' },
  { value: '35', label: '35 分' },
  { value: '40', label: '40 分' },
  { value: '45', label: '45 分' },
  { value: '50', label: '50 分' },
  { value: '55', label: '55 分' },
  { value: '59', label: '59 分' },
  { value: '*/5', label: '每5分钟' },
  { value: '*/10', label: '每10分钟' },
  { value: '*/15', label: '每15分钟' },
  { value: '*/30', label: '每30分钟' },
];

const HOUR_OPTIONS = [
  { value: '*', label: '每小时' },
  { value: '0', label: '0 点（凌晨）' },
  { value: '8', label: '8 点（早盘前）' },
  { value: '9', label: '9 点（开盘）' },
  { value: '10', label: '10 点' },
  { value: '11', label: '11 点（早盘尾）' },
  { value: '13', label: '13 点（午盘开）' },
  { value: '14', label: '14 点' },
  { value: '15', label: '15 点（收盘）' },
  { value: '16', label: '16 点' },
  { value: '9,10,13,14', label: '9,10,13,14点（盘中）' },
  { value: '9-11,13-14', label: '9-11,13-14点（全盘中）' },
];

const DAY_OPTIONS = [
  { value: '*', label: '每天' },
  { value: '1', label: '1日' },
  { value: '15', label: '15日' },
  { value: 'L', label: '月末' },
];

const DAY_OF_WEEK_OPTIONS = [
  { value: '*', label: '每天（1-7）' },
  { value: '1-5', label: '周一至周五（工作日）' },
  { value: '1', label: '周一' },
  { value: '2', label: '周二' },
  { value: '3', label: '周三' },
  { value: '4', label: '周四' },
  { value: '5', label: '周五' },
  { value: '6', label: '周六' },
  { value: '0', label: '周日' },
];

const TIMZEONE_OPTIONS = [
  { value: 'Asia/Shanghai', label: '北京时间 (UTC+8)' },
  { value: 'Asia/Tokyo', label: '东京时间 (UTC+9)' },
  { value: 'America/New_York', label: '纽约时间 (UTC-5)' },
  { value: 'UTC', label: 'UTC' },
];

function parseCron(expr: string): CronField {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) {
    return { minute: '*', hour: '*', day: '*', month: '*', dayOfWeek: '*' };
  }
  return {
    minute: parts[0],
    hour: parts[1],
    day: parts[2],
    month: parts[3],
    dayOfWeek: parts[4],
  };
}

function cronToHuman(expr: string, locale: 'zh' | 'en' = 'zh'): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;

  const [minute, hour, day, , dayOfWeek] = parts;

  if (locale === 'zh') {
    const timePart = (() => {
      const h = hour === '*' ? '每时' : `${hour}时`;
      const m = minute === '*' ? '每分' : minute.includes('/') ? `每${minute.split('/')[1]}分钟` : `${minute}分`;
      return `${h} ${m}`;
    })();

    const dayPart = (() => {
      if (day !== '*') return ` | 每月${day}日`;
      return '';
    })();

    const dowPart = (() => {
      if (dayOfWeek === '*') return ' | 每天';
      if (dayOfWeek === '1-5') return ' | 周一至周五';
      if (dayOfWeek === '0' || dayOfWeek === '6') return ' | 周末';
      return ` | 星期${dayOfWeek}`;
    })();

    return `${timePart}${dowPart}${dayPart}`;
  }

  // English
  const timePart = (() => {
    const h = hour === '*' ? 'Every hour' : `${hour}:`;
    const m = minute === '*' ? 'Every minute' : minute.includes('/') ? `Every ${minute.split('/')[1]} mins` : minute;
    return `${h}${hour !== '*' && minute !== '*' ? m : ''}`;
  })();

  const dowPart = (() => {
    if (dayOfWeek === '*') return 'every day';
    if (dayOfWeek === '1-5') return 'weekdays';
    return `day ${dayOfWeek}`;
  })();

  return `${timePart} on ${dowPart}`;
}

export default function CronEditor({ value, onChange }: CronEditorProps) {
  const [mode, setMode] = useState<'visual' | 'preset' | 'raw'>('visual');
  const [fields, setFields] = useState<CronField>(parseCron(value.expr));
  const [rawExpr, setRawExpr] = useState(value.expr);
  const [rawError, setRawError] = useState<string | null>(null);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);

  useEffect(() => {
    setFields(parseCron(value.expr));
    setRawExpr(value.expr);
    setRawError(null);
  }, [value.expr]);

  const currentExpr = useMemo(() => {
    if (mode === 'visual') {
      return `${fields.minute} ${fields.hour} ${fields.day} ${fields.month} ${fields.dayOfWeek}`;
    }
    if (mode === 'preset' && selectedPreset) {
      return selectedPreset;
    }
    return rawExpr;
  }, [mode, fields, selectedPreset, rawExpr]);

  const humanReadable = useMemo(() => cronToHuman(currentExpr), [currentExpr]);

  const updateField = useCallback((field: keyof CronField, fieldValue: string) => {
    setFields(prev => {
      const next = { ...prev, [field]: fieldValue };
      const expr = `${next.minute} ${next.hour} ${next.day} ${next.month} ${next.dayOfWeek}`;
      onChange({ type: 'cron', expr, timezone: value.timezone });
      return next;
    });
  }, [onChange, value.timezone]);

  const handlePresetSelect = useCallback((expr: string) => {
    setSelectedPreset(expr);
    onChange({ type: 'cron', expr, timezone: value.timezone });
  }, [onChange, value.timezone]);

  const handleRawChange = useCallback((newExpr: string) => {
    setRawExpr(newExpr);
    // Validate
    const trimmed = newExpr.trim();
    if (!trimmed) {
      setRawError('Cron 表达式不能为空');
      return;
    }
    const parts = trimmed.split(/\s+/);
    if (parts.length !== 5) {
      setRawError('Cron 表达式必须有5个字段（分 时 日 月 星期）');
      return;
    }
    // Basic field validation
    const valid = validateCronField(parts[0], 0, 59) &&
      validateCronField(parts[1], 0, 23) &&
      validateCronField(parts[2], 1, 31) &&
      validateCronField(parts[3], 1, 12) &&
      validateCronField(parts[4], 0, 7);

    if (!valid) {
      setRawError('Cron 表达式格式不正确');
      return;
    }

    setRawError(null);
    onChange({ type: 'cron', expr: trimmed, timezone: value.timezone });
  }, [onChange, value.timezone]);

  const handleTimezoneChange = useCallback((tz: string) => {
    onChange({ type: 'cron', expr: currentExpr, timezone: tz });
  }, [onChange, currentExpr]);

  return (
    <div className="space-y-4">
      {/* Mode Tabs */}
      <div className="flex border-b border-gray-700">
        {([
          { key: 'visual', label: '可视化构建', icon: Clock },
          { key: 'preset', label: '常用预设', icon: ChevronDown },
          { key: 'raw', label: '直接输入', icon: Code },
        ] as const).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setMode(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm border-b-2 transition-colors ${
              mode === tab.key
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            }`}
          >
            <tab.icon size={14} />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Human-Readable Preview */}
      <div className="flex items-center gap-2 p-3 bg-blue-900/20 border border-blue-800/50 rounded-lg">
        <Clock size={16} className="text-blue-400 flex-shrink-0" />
        <span className="text-sm text-blue-300">{humanReadable}</span>
        <code className="ml-auto text-xs text-gray-500 bg-dark-300 px-2 py-0.5 rounded">{currentExpr}</code>
      </div>

      {/* Timezone Selector */}
      <div className="flex items-center gap-2">
        <label className="text-sm text-gray-400 flex-shrink-0">时区:</label>
        <select
          value={value.timezone}
          onChange={(e) => handleTimezoneChange(e.target.value)}
          className="flex-1 bg-dark-300 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          {TIMZEONE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {/* Visual Mode */}
      {mode === 'visual' && (
        <div className="grid grid-cols-2 gap-3">
          <CronSelectField
            label="分钟"
            value={fields.minute}
            options={MINUTE_OPTIONS}
            onChange={(v) => updateField('minute', v)}
          />
          <CronSelectField
            label="小时"
            value={fields.hour}
            options={HOUR_OPTIONS}
            onChange={(v) => updateField('hour', v)}
          />
          <CronSelectField
            label="日期"
            value={fields.day}
            options={DAY_OPTIONS}
            onChange={(v) => updateField('day', v)}
          />
          <CronSelectField
            label="星期"
            value={fields.dayOfWeek}
            options={DAY_OF_WEEK_OPTIONS}
            onChange={(v) => updateField('dayOfWeek', v)}
          />
          <div className="col-span-2">
            <label className="block text-sm text-gray-400 mb-1">月份</label>
            <input
              type="text"
              value={fields.month}
              onChange={(e) => updateField('month', e.target.value)}
              className="w-full bg-dark-300 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
              placeholder="* (每月)"
            />
            <p className="text-xs text-gray-500 mt-1">通常为 *（每月），也支持 1-12 或具体月份</p>
          </div>
        </div>
      )}

      {/* Preset Mode */}
      {mode === 'preset' && (
        <div className="grid grid-cols-2 gap-2 max-h-60 overflow-y-auto">
          {PRESETS.map((preset) => (
            <button
              key={preset.expr}
              onClick={() => handlePresetSelect(preset.expr)}
              className={`text-left p-3 rounded border text-sm transition-colors ${
                currentExpr === preset.expr
                  ? 'border-blue-500 bg-blue-900/20 text-blue-300'
                  : 'border-gray-700 bg-dark-300 hover:border-gray-500 text-gray-300'
              }`}
            >
              <div className="font-medium">{preset.label}</div>
              <code className="text-xs text-gray-500 mt-0.5 block">{preset.expr}</code>
            </button>
          ))}
        </div>
      )}

      {/* Raw Input Mode */}
      {mode === 'raw' && (
        <div>
          <div className="flex gap-2">
            <input
              type="text"
              value={rawExpr}
              onChange={(e) => handleRawChange(e.target.value)}
              className={`flex-1 bg-dark-300 border rounded px-3 py-2 text-sm font-mono text-white focus:outline-none ${
                rawError ? 'border-red-500 focus:border-red-400' : 'border-gray-600 focus:border-blue-500'
              }`}
              placeholder="分 时 日 月 星期（例如：30 9 * * 1-5）"
            />
          </div>
          {rawError && (
            <p className="text-red-400 text-xs mt-1">{rawError}</p>
          )}
          <div className="mt-2 p-2 bg-dark-300 rounded border border-gray-700">
            <p className="text-xs text-gray-500 leading-relaxed">
              <strong>格式：</strong>分 时 日 月 星期<br />
              <strong>示例：</strong><br />
              · <code>0 9 * * 1-5</code> — 工作日 9:00<br />
              · <code>*/10 * * * *</code> — 每10分钟<br />
              · <code>30 9,10,13,14 * * 1-5</code> — 工作日 9:30,10:30,13:30,14:30
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// Helper: Validate a single cron field
function validateCronField(value: string, min: number, max: number): boolean {
  if (value === '*') return true;
  // */step
  if (/^\*\/\d+$/.test(value)) {
    const step = parseInt(value.split('/')[1]);
    return step >= 1 && step <= max;
  }
  // single value
  if (/^\d+$/.test(value)) {
    const num = parseInt(value);
    return num >= min && num <= max;
  }
  // range: a-b
  if (/^\d+-\d+$/.test(value)) {
    const [a, b] = value.split('-').map(Number);
    return a >= min && b <= max && a <= b;
  }
  // list: a,b,c
  if (/^[\d,-]+$/.test(value)) {
    return value.split(',').every(part => {
      if (/^\d+$/.test(part)) {
        const num = parseInt(part);
        return num >= min && num <= max;
      }
      if (/^\d+-\d+$/.test(part)) {
        const [a, b] = part.split('-').map(Number);
        return a >= min && b <= max && a <= b;
      }
      return false;
    });
  }
  return false;
}

// Reusable cron field select component
function CronSelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  const isCustom = !options.some((o) => o.value === value);

  return (
    <div>
      <label className="block text-sm text-gray-400 mb-1">{label}</label>
      <select
        value={isCustom ? '__custom__' : value}
        onChange={(e) => {
          if (e.target.value !== '__custom__') {
            onChange(e.target.value);
          }
        }}
        className="w-full bg-dark-300 border border-gray-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
        <option value="__custom__">自定义...</option>
      </select>
      {isCustom && (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-dark-300 border border-gray-600 rounded px-3 py-1.5 text-sm font-mono text-white mt-1 focus:outline-none focus:border-blue-500"
          placeholder={`例如: *, 1-5, */10`}
        />
      )}
    </div>
  );
}
