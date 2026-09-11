import { Skeleton } from '../ui/Skeleton';
import { fmtInt } from '../../utils/format';

export interface DistributionBucket {
  bucket: string;
  count: number;
}

export interface DistributionChartProps {
  data: DistributionBucket[];
  loading?: boolean;
  height?: number;
  ariaLabel?: string;
}

/** 判断分桶是否为负收益区间（如「-5%~-2%」「<-5%」） */
function isNegativeBucket(bucket: string): boolean {
  const text = bucket.trim();
  if (text.startsWith('+') || text.startsWith('>')) return false;
  return text.startsWith('<') || text.startsWith('-') || text.startsWith('−');
}

/**
 * 收益分布：横向分桶条形图。
 * 配色与上方「红=盈利 / 绿=亏损」的 A 股约定保持一致：正收益桶用 --up，负收益桶用 --down；
 * 0 样本的桶走中性色并明确标注「暂无样本」。
 */
export function DistributionChart({
  data,
  loading = false,
  height = 220,
  ariaLabel = '收益分布图',
}: DistributionChartProps) {
  if (loading) return <Skeleton variant="rect" height={height} />;

  const items = Array.isArray(data) ? data.filter((d) => d && Number.isFinite(d.count)) : [];
  const total = items.reduce((sum, d) => sum + d.count, 0);
  const max = items.length ? Math.max(...items.map((d) => d.count)) : 0;

  const rowH = items.length ? Math.max(16, Math.min(28, (height - 40) / items.length)) : 22;

  return (
    <div role="img" aria-label={ariaLabel} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div className="row" style={{ height: 20, gap: 10, color: 'var(--text-3)', fontSize: 11 }}>
        <span style={{ flex: '0 0 88px', textAlign: 'right' }}>收益区间</span>
        <span style={{ flex: '1 1 auto' }}>分布</span>
        <span style={{ flex: '0 0 92px', textAlign: 'right' }}>笔数 / 占比</span>
      </div>

      {items.length === 0 && <div className="chart-empty">暂无分布数据</div>}

      {items.map((item) => {
        const pct = max > 0 ? item.count / max : 0;
        const share = total > 0 ? (item.count / total) * 100 : 0;
        const negative = isNegativeBucket(item.bucket);
        const color = item.count > 0 ? (negative ? 'var(--down)' : 'var(--up)') : 'var(--arc-muted)';
        return (
          <div
            key={item.bucket}
            className="row"
            style={{ height: rowH, gap: 10 }}
            title={`${item.bucket}：${fmtInt(item.count)} 笔（${share.toFixed(1)}%）`}
          >
            <span
              className="mono fs-12 text-2"
              style={{ flex: '0 0 88px', textAlign: 'right', whiteSpace: 'nowrap' }}
            >
              {item.bucket}
            </span>
            <span
              className="bar"
              style={{ flex: '1 1 auto', height: 12, borderRadius: 3 }}
              aria-hidden="true"
            >
              <span
                style={{
                  display: 'block',
                  height: '100%',
                  width: `${Math.max(pct * 100, item.count > 0 ? 2 : 0)}%`,
                  background: color,
                  borderRadius: 3,
                }}
              />
            </span>
            <span className="mono fs-12" style={{ flex: '0 0 92px', textAlign: 'right' }}>
              {item.count > 0 ? (
                <>
                  {fmtInt(item.count)}
                  <span className="text-3" style={{ marginLeft: 6 }}>
                    {share.toFixed(1)}%
                  </span>
                </>
              ) : (
                <span className="fs-11 text-3">暂无样本</span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
