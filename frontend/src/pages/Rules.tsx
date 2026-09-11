import { useEffect, useMemo, useState } from 'react';
import { api, toErrorMessage } from '../api/client';
import type { CriteriaGroupKey, RuleSet, SignalKey } from '../api/types';
import { useApi } from '../hooks/useApi';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { SkeletonRows } from '../components/ui/Skeleton';
import { useToast } from '../components/ui/Toast';
import { SIGNAL_META, SIGNAL_KEYS } from '../utils/constants';

const GROUP_META: Record<CriteriaGroupKey, { title: string; sub: string; icon: string; tone: string }> = {
  limitUp: { title: '选股标准', sub: '涨停当日的硬性门槛', icon: '板', tone: '' },
  entry: { title: '五大信号', sub: '回调阶段的共振确认', icon: '信', tone: 'gold' },
  discipline: { title: '交易纪律', sub: '执行层面必须遵守的规则', icon: '律', tone: 'teal' },
  avoid: { title: '避坑要点', sub: '命中即一票否决的情形', icon: '避', tone: 'danger' },
};

const GROUP_ORDER: CriteriaGroupKey[] = ['limitUp', 'entry', 'discipline', 'avoid'];

interface NumberFieldProps {
  id: string;
  label: string;
  hint?: string;
  value: number;
  step?: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}

function NumberField({ id, label, hint, value, step = 0.01, min, max, onChange }: NumberFieldProps) {
  const safe = Number.isFinite(value) ? value : 0;
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        <span>{label}</span>
        {hint && <span className="fs-11 text-3">{hint}</span>}
      </label>
      <input
        id={id}
        className="input num"
        type="number"
        value={safe}
        step={step}
        min={min}
        max={max}
        onChange={(e) => {
          const next = Number(e.target.value);
          if (Number.isFinite(next)) onChange(next);
        }}
      />
    </div>
  );
}

/** 战法规则与参数：规则全文展示 + 权重与阈值在线调整 */
export default function Rules() {
  const toast = useToast();
  const rules = useApi((signal) => api.rules(signal), []);
  const [form, setForm] = useState<RuleSet | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (rules.data?.ruleSet) setForm(rules.data.ruleSet);
  }, [rules.data]);

  const weightTotal = useMemo(() => {
    if (!form) return 0;
    return SIGNAL_KEYS.reduce((sum, key) => sum + (form.weights?.[key] ?? 0), 0);
  }, [form]);

  const totalOk = Math.abs(weightTotal - 1) < 0.005;

  const updateWeight = (key: SignalKey, value: number) => {
    setForm((prev) =>
      prev ? { ...prev, weights: { ...prev.weights, [key]: Math.max(0, Math.min(1, value)) } } : prev,
    );
  };

  const normalizeWeights = () => {
    setForm((prev) => {
      if (!prev) return prev;
      const total = SIGNAL_KEYS.reduce((sum, key) => sum + (prev.weights?.[key] ?? 0), 0);
      if (total <= 0) return prev;
      const scaled: Record<string, number> = {};
      let acc = 0;
      SIGNAL_KEYS.forEach((key, index) => {
        if (index === SIGNAL_KEYS.length - 1) {
          scaled[key] = Number((1 - acc).toFixed(2));
        } else {
          const value = Number((((prev.weights?.[key] ?? 0) / total)).toFixed(2));
          scaled[key] = value;
          acc += value;
        }
      });
      return { ...prev, weights: { ...prev.weights, ...scaled } };
    });
    toast.info('权重已按当前比例归一化到 1.00');
  };

  const save = async () => {
    if (!form || !totalOk) return;
    setSaving(true);
    try {
      const updated = await api.updateRules({
        weights: form.weights,
        buyScore: form.buyScore,
        watchScore: form.watchScore,
        maxHighPositionRatio: form.maxHighPositionRatio,
        minVolRatio: form.minVolRatio,
        minTurnover: form.minTurnover,
        maxTurnover: form.maxTurnover,
        minPullbackDays: form.minPullbackDays,
        maxPullbackDays: form.maxPullbackDays,
      });
      setForm(updated);
      toast.success('策略参数已保存，服务端已热更新');
      rules.refetch();
    } catch (err) {
      toast.error(`保存失败：${toErrorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const resetLocal = () => {
    if (rules.data?.ruleSet) {
      setForm(rules.data.ruleSet);
      toast.info('已还原为服务端当前参数');
    }
  };

  return (
    <>
      <PageHeader
        title="战法规则与参数"
        sub="选股标准、五大信号、交易纪律与避坑要点全文；权重与阈值可在线调整并热更新"
        actions={
          <>
            <Button loading={rules.refreshing} onClick={rules.refetch}>
              重新载入
            </Button>
            <Button variant="primary" disabled={!form || !totalOk} loading={saving} onClick={save}>
              保存参数
            </Button>
          </>
        }
      />

      {rules.error && rules.data && (
        <div className="mt-2">
          <InlineAlert message={rules.error} onRetry={rules.refetch} hint="以下为上一次成功获取的规则全文。" />
        </div>
      )}

      {rules.error && !rules.data && (
        <Card className="mt-4">
          <ErrorState message={rules.error} onRetry={rules.refetch} />
        </Card>
      )}

      {rules.loading && !rules.data && (
        <Card className="mt-4" title="规则载入中">
          <SkeletonRows rows={6} cols={3} />
        </Card>
      )}

      {rules.data && (
        <>
          <div className="rules-grid mt-4">
            {GROUP_ORDER.map((groupKey) => {
              const meta = GROUP_META[groupKey];
              const items = rules.data?.criteria?.[groupKey] ?? [];
              return (
                <Card key={groupKey} title={meta.title} sub={meta.sub} flush>
                  {items.length === 0 ? (
                    <div className="state" style={{ minHeight: 120 }}>
                      <div className="state-desc">该分组暂无规则条目</div>
                    </div>
                  ) : (
                    <ul className="rule-list">
                      {items.map((item) => (
                        <li className="rule-item" key={item.key}>
                          <span className={`rule-icon ${meta.tone}`} aria-hidden="true">
                            {meta.icon}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div className="rule-name">
                              <span>{item.name}</span>
                              {item.enabled ? (
                                <Badge tone="teal" dot>
                                  生效
                                </Badge>
                              ) : (
                                <Badge tone="neutral">未启用</Badge>
                              )}
                            </div>
                            <div className="rule-desc">{item.desc}</div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              );
            })}
          </div>

          <div className="grid-2 mt-4">
            {/* 权重 */}
            <Card
              title="信号权重"
              sub="五项权重合计必须等于 1.00，否则无法保存"
              extra={
                <Button size="sm" onClick={normalizeWeights} disabled={!form}>
                  一键归一化
                </Button>
              }
            >
              {form ? (
                <div className="col" style={{ gap: 12 }}>
                  {SIGNAL_KEYS.map((key) => {
                    const value = form.weights?.[key] ?? 0;
                    return (
                      <div className="weight-row" key={key}>
                        <span className="weight-label" title={SIGNAL_META[key].desc}>
                          {SIGNAL_META[key].label}
                        </span>
                        <input
                          className="range"
                          type="range"
                          min={0}
                          max={1}
                          step={0.01}
                          value={value}
                          aria-label={`${SIGNAL_META[key].short} 权重`}
                          onChange={(e) => updateWeight(key, Number(e.target.value))}
                        />
                        <span className="weight-val">{(value * 100).toFixed(0)}%</span>
                      </div>
                    );
                  })}

                  <div className={`weight-total ${totalOk ? 'ok' : 'bad'}`}>
                    <span>
                      权重合计
                      <span className="fs-11 text-3" style={{ marginLeft: 8 }}>
                        {totalOk ? '符合要求' : '必须等于 1.00（100%）'}
                      </span>
                    </span>
                    <span className="mono strong" style={{ fontSize: 16 }}>
                      {weightTotal.toFixed(2)}
                    </span>
                  </div>
                </div>
              ) : (
                <SkeletonRows rows={5} cols={2} />
              )}
            </Card>

            {/* 阈值参数 */}
            <Card title="评分与阈值参数" sub={`规则版本 ${rules.data.ruleSet?.version ?? '—'}`}>
              {form ? (
                <div className="col" style={{ gap: 16 }}>
                  <div className="param-grid">
                    <NumberField
                      id="rule-buy-score"
                      label="可低吸评分线"
                      hint="0 ~ 100"
                      value={form.buyScore}
                      step={0.5}
                      min={0}
                      max={100}
                      onChange={(v) => setForm({ ...form, buyScore: v })}
                    />
                    <NumberField
                      id="rule-watch-score"
                      label="观察评分线"
                      hint="0 ~ 100"
                      value={form.watchScore}
                      step={0.5}
                      min={0}
                      max={100}
                      onChange={(v) => setForm({ ...form, watchScore: v })}
                    />
                    <NumberField
                      id="rule-high-pos"
                      label="高位板区间上限"
                      hint="0 ~ 1"
                      value={form.maxHighPositionRatio}
                      onChange={(v) => setForm({ ...form, maxHighPositionRatio: v })}
                    />
                    <NumberField
                      id="rule-min-volratio"
                      label="最低量比"
                      hint="涨停日量能门槛"
                      value={form.minVolRatio}
                      step={0.1}
                      onChange={(v) => setForm({ ...form, minVolRatio: v })}
                    />
                    <NumberField
                      id="rule-min-turnover"
                      label="最低换手率"
                      hint="%"
                      value={form.minTurnover}
                      step={0.1}
                      onChange={(v) => setForm({ ...form, minTurnover: v })}
                    />
                    <NumberField
                      id="rule-max-turnover"
                      label="最高换手率"
                      hint="%"
                      value={form.maxTurnover}
                      step={0.1}
                      onChange={(v) => setForm({ ...form, maxTurnover: v })}
                    />
                    <NumberField
                      id="rule-min-pullback"
                      label="最短回调天数"
                      hint="天"
                      value={form.minPullbackDays}
                      step={1}
                      onChange={(v) => setForm({ ...form, minPullbackDays: v })}
                    />
                    <NumberField
                      id="rule-max-pullback"
                      label="最长回调天数"
                      hint="天"
                      value={form.maxPullbackDays}
                      step={1}
                      onChange={(v) => setForm({ ...form, maxPullbackDays: v })}
                    />
                  </div>

                  {form.buyScore <= form.watchScore && (
                    <Badge tone="danger" dot>
                      可低吸评分线应高于观察评分线
                    </Badge>
                  )}
                  {form.minTurnover >= form.maxTurnover && (
                    <Badge tone="danger" dot>
                      最低换手率必须小于最高换手率
                    </Badge>
                  )}
                  {form.minPullbackDays >= form.maxPullbackDays && (
                    <Badge tone="danger" dot>
                      最短回调天数必须小于最长回调天数
                    </Badge>
                  )}

                  <div className="row" style={{ gap: 8 }}>
                    <Button variant="primary" loading={saving} disabled={!totalOk} onClick={save}>
                      保存并热更新
                    </Button>
                    <Button onClick={resetLocal}>还原</Button>
                  </div>

                  <span className="fs-11 text-3" style={{ lineHeight: 1.8 }}>
                    保存后服务端会持久化到数据目录的 rules.json 并立即生效，下一次扫描即按新参数执行。
                    参数改动会影响信号评分与结论分布，建议小步调整并配合回测验证。
                  </span>
                </div>
              ) : (
                <SkeletonRows rows={4} cols={2} />
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
