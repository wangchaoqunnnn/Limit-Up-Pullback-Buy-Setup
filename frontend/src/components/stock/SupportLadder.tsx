import clsx from 'clsx';
import type { SupportLevels } from '../../api/types';
import { fmtPct, fmtPrice, isNum } from '../../utils/format';
import { EmptyState } from '../ui/EmptyState';

export interface SupportLadderProps {
  support: SupportLevels | null | undefined;
  lastClose: number | null | undefined;
  ariaLabel?: string;
}

interface LadderLevel {
  name: string;
  price: number;
  kind: 'limitOpen' | 'strongHalf' | 'ma';
  isActive: boolean;
}

/** 支撑位阶梯：由下到上排列涨停开盘价 / 实体半分位 / MA5 / MA10 / MA20，并标注现价位置与距离 */
export function SupportLadder({ support, lastClose, ariaLabel = '支撑位阶梯' }: SupportLadderProps) {
  if (!support) {
    return <EmptyState title="暂无支撑位数据" desc="该标的尚未生成支撑位分析。" />;
  }

  const raw: LadderLevel[] = [];
  const push = (name: string, price: number | null, kind: LadderLevel['kind']) => {
    if (isNum(price)) raw.push({ name, price, kind, isActive: support.activeSupportName === name });
  };
  push('涨停开盘价', support.limitOpen, 'limitOpen');
  push('实体半分位', support.strongHalf, 'strongHalf');
  push('MA20', support.ma20, 'ma');
  push('MA10', support.ma10, 'ma');
  push('MA5', support.ma5, 'ma');

  const levels = raw.sort((a, b) => a.price - b.price);

  if (levels.length === 0) {
    return <EmptyState title="暂无有效支撑位" desc="当前价格区间内未计算得到支撑位。" />;
  }

  const close = isNum(lastClose) ? lastClose : null;
  const allPrices = levels.map((l) => l.price);
  if (close !== null) allPrices.push(close);
  const min = Math.min(...allPrices);
  const max = Math.max(...allPrices);
  const span = max - min || Math.max(max * 0.01, 0.01);
  const posOf = (price: number) => Math.max(0, Math.min(1, (price - min) / span));

  // 现价按价格顺序插入（由下到上）
  const rows: Array<LadderLevel | { name: '现价'; price: number; kind: 'current'; isActive: false }> = [...levels];
  if (close !== null) {
    let inserted = false;
    for (let i = 0; i < rows.length; i += 1) {
      if (close < (rows[i] as LadderLevel).price) {
        rows.splice(i, 0, { name: '现价', price: close, kind: 'current', isActive: false });
        inserted = true;
        break;
      }
    }
    if (!inserted) rows.push({ name: '现价', price: close, kind: 'current', isActive: false });
  }

  return (
    <div className="ladder" role="table" aria-label={ariaLabel}>
      {rows.map((row, index) => {
        const isCurrent = row.kind === 'current';
        const distance = close !== null && !isCurrent && row.price > 0 ? (close - row.price) / row.price : null;
        return (
          <div
            key={`${row.name}-${row.price}-${index}`}
            role="row"
            className={clsx('ladder-row', isCurrent && 'current', !isCurrent && row.isActive && 'active')}
          >
            <span className="ladder-label" role="cell">
              {isCurrent ? (
                <span className="gold-text strong">现价</span>
              ) : (
                <>
                  <span className="ellipsis">{row.name}</span>
                  {row.isActive && <span className="tag tag-brand">生效</span>}
                </>
              )}
            </span>

            <span className="ladder-track" role="cell" aria-label={`相对位置 ${(posOf(row.price) * 100).toFixed(0)}%`}>
              <span
                className="ladder-fill"
                style={{
                  width: `${Math.max(2, posOf(row.price) * 100)}%`,
                  background: isCurrent
                    ? 'linear-gradient(90deg, color-mix(in srgb, var(--gold) 55%, transparent), var(--gold))'
                    : undefined,
                }}
              />
            </span>

            <span className="ladder-price" role="cell">
              <span className={clsx(isCurrent && 'gold-text')}>{fmtPrice(row.price)}</span>
              {distance !== null && (
                <span className="fs-11 text-3" style={{ marginLeft: 6 }}>
                  {distance >= 0 ? '上方 ' : '下方 '}
                  {fmtPct(Math.abs(distance), 2, false)}
                </span>
              )}
            </span>
          </div>
        );
      })}

      <div className="row fs-11 text-3" style={{ justifyContent: 'space-between', marginTop: 4 }}>
        <span>阶梯由下至上：低位支撑 → 高位支撑</span>
        {isNum(support.distanceToSupportPct) && (
          <span className="mono">
            距生效支撑 {fmtPct(support.distanceToSupportPct)}
            {support.activeSupportName ? `（${support.activeSupportName}）` : ''}
          </span>
        )}
      </div>
    </div>
  );
}
