import type { TradePlan } from '../../api/types';
import { fmtPct, fmtPrice, isNum } from '../../utils/format';
import { EmptyState } from '../ui/EmptyState';

export interface TradePlanCardProps {
  plan: TradePlan | null | undefined;
  lastClose?: number | null;
}

interface Level {
  label: string;
  price: number;
  tone: 'stop' | 'buy' | 'tp';
}

/** 分批交易计划：买入区间 / 止损 / 两档止盈 / 盈亏比 / 建议仓位，附价格轴可视化 */
export function TradePlanCard({ plan, lastClose }: TradePlanCardProps) {
  if (!plan) {
    return <EmptyState title="暂无交易计划" desc="命中战法标的后将自动生成分批交易计划。" />;
  }

  const levels: Level[] = [];
  if (isNum(plan.stopLoss)) levels.push({ label: '止损', price: plan.stopLoss, tone: 'stop' });
  if (isNum(plan.buyLow)) levels.push({ label: '买1', price: plan.buyLow, tone: 'buy' });
  if (isNum(plan.buyHigh)) levels.push({ label: '买2', price: plan.buyHigh, tone: 'buy' });
  if (isNum(plan.takeProfit1)) levels.push({ label: '止盈1', price: plan.takeProfit1, tone: 'tp' });
  if (isNum(plan.takeProfit2)) levels.push({ label: '止盈2', price: plan.takeProfit2, tone: 'tp' });

  const prices = levels.map((l) => l.price);
  if (isNum(lastClose)) prices.push(lastClose);
  const min = prices.length ? Math.min(...prices) : 0;
  const max = prices.length ? Math.max(...prices) : 1;
  const span = max - min || Math.max(max * 0.01, 0.01);
  const posOf = (price: number) => Math.max(0, Math.min(100, ((price - min) / span) * 100));

  const toneColor = (tone: Level['tone']) =>
    tone === 'stop' ? 'var(--down)' : tone === 'buy' ? 'var(--brand)' : 'var(--up)';

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="plan-grid">
        <div className="plan-item">
          <div className="plan-k">买入区间</div>
          <div className="plan-v brand-text">
            {fmtPrice(plan.buyLow)} ~ {fmtPrice(plan.buyHigh)}
          </div>
          <div className="plan-note">分 {(plan.batchCount ?? 1) > 0 ? plan.batchCount : 1} 批建仓</div>
        </div>
        <div className="plan-item">
          <div className="plan-k">止损位</div>
          <div className="plan-v down">{fmtPrice(plan.stopLoss)}</div>
          <div className="plan-note">
            {isNum(plan.stopLoss) && isNum(plan.buyLow) && plan.buyLow > 0
              ? `距买入下限 ${fmtPct((plan.stopLoss - plan.buyLow) / plan.buyLow)}`
              : '破位即离场'}
          </div>
        </div>
        <div className="plan-item">
          <div className="plan-k">止盈一 / 二</div>
          <div className="plan-v up">
            {fmtPrice(plan.takeProfit1)} / {fmtPrice(plan.takeProfit2)}
          </div>
          <div className="plan-note">分批止盈</div>
        </div>
        <div className="plan-item">
          <div className="plan-k">盈亏比</div>
          <div className="plan-v gold-text">{isNum(plan.riskReward) ? plan.riskReward.toFixed(2) : '—'}</div>
          <div className="plan-note">≥ 2 为佳</div>
        </div>
        <div className="plan-item">
          <div className="plan-k">建议仓位</div>
          <div className="plan-v">{isNum(plan.positionPct) ? `${plan.positionPct}%` : '—'}</div>
          <div className="plan-note">单票上限</div>
        </div>
      </div>

      {levels.length > 0 && (
        <div>
          <div className="relative" style={{ height: 44, marginTop: 6 }}>
            <div
              className="bar"
              style={{ position: 'absolute', top: 18, left: 0, right: 0, height: 4 }}
              aria-hidden="true"
            />
            {levels.map((level) => (
              <div
                key={level.label}
                style={{
                  position: 'absolute',
                  left: `${posOf(level.price)}%`,
                  top: 10,
                  transform: 'translateX(-50%)',
                  textAlign: 'center',
                }}
                title={`${level.label} ${fmtPrice(level.price)}`}
              >
                <span className="fs-11 text-3 nowrap">{level.label}</span>
                <div
                  style={{
                    width: 2,
                    height: 12,
                    margin: '2px auto',
                    background: toneColor(level.tone),
                    borderRadius: 2,
                  }}
                />
                <span className="mono fs-11 nowrap" style={{ color: toneColor(level.tone) }}>
                  {fmtPrice(level.price)}
                </span>
              </div>
            ))}
            {isNum(lastClose) && (
              <div
                style={{
                  position: 'absolute',
                  left: `${posOf(lastClose)}%`,
                  top: -2,
                  transform: 'translateX(-50%)',
                }}
                title={`现价 ${fmtPrice(lastClose)}`}
              >
                <span className="mono fs-11 gold-text nowrap">现价 {fmtPrice(lastClose)}</span>
                <div style={{ width: 1, height: 30, margin: '1px auto', background: 'var(--gold)' }} />
              </div>
            )}
          </div>
          <div className="fs-11 text-3" style={{ marginTop: 2 }}>
            止损 → 买入区间 → 止盈两档的相对位置（按当前价位等比映射）
          </div>
        </div>
      )}
    </div>
  );
}
