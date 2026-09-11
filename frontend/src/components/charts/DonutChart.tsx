import { useId } from 'react';
import type { ReactNode } from 'react';
import { fmtInt } from '../../utils/format';

export interface DonutSlice {
  label: string;
  value: number;
  color: string;
}

export interface DonutChartProps {
  data: DonutSlice[];
  size?: number;
  thickness?: number;
  centerLabel?: ReactNode;
  centerValue?: ReactNode;
  /** 非零扇区的最小可见角度（度），避免 2% 这类小占比肉眼不可辨 */
  minSweepDeg?: number;
  ariaLabel?: string;
}

interface ArcPath {
  d: string;
  color: string;
  label: string;
  value: number;
}

function polar(cx: number, cy: number, r: number, angle: number) {
  const rad = ((angle - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, rOuter: number, rInner: number, start: number, end: number): string {
  const large = end - start > 180 ? 1 : 0;
  const p1 = polar(cx, cy, rOuter, start);
  const p2 = polar(cx, cy, rOuter, end);
  const p3 = polar(cx, cy, rInner, end);
  const p4 = polar(cx, cy, rInner, start);
  return [
    `M${p1.x.toFixed(2)},${p1.y.toFixed(2)}`,
    `A${rOuter},${rOuter} 0 ${large} 1 ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`,
    `L${p3.x.toFixed(2)},${p3.y.toFixed(2)}`,
    `A${rInner},${rInner} 0 ${large} 0 ${p4.x.toFixed(2)},${p4.y.toFixed(2)}`,
    'Z',
  ].join(' ');
}

/** 环形占比图：评分分布（可低吸 / 观察 / 放弃） */
export function DonutChart({
  data,
  size = 172,
  thickness = 18,
  centerLabel,
  centerValue,
  minSweepDeg = 5,
  ariaLabel = '占比环形图',
}: DonutChartProps) {
  const gradientId = useId().replace(/[:]/g, '');
  const total = data.reduce((sum, item) => sum + (Number.isFinite(item.value) ? item.value : 0), 0);
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = size / 2 - 2;
  const rInner = rOuter - thickness;

  // 先按真实占比算角度，再给小扇区补足最小可见弧长；
  // 若补足后总和超过 360°，按比例整体回缩，保证首尾严格闭合。
  const positive = data.filter((item) => (item.value || 0) > 0);
  const rawSweeps = positive.map((item) => ((item.value || 0) / total) * 360);
  const softSweeps = rawSweeps.map((sweep) => Math.max(sweep, minSweepDeg));
  const softSum = softSweeps.reduce((sum, sweep) => sum + sweep, 0);
  const shrink = softSum > 360 ? 360 / softSum : 1;

  const arcs: ArcPath[] = [];
  let cursor = 0;
  positive.forEach((item, index) => {
    const sweep = softSweeps[index] * shrink;
    const start = cursor;
    const end = Math.min(360, start + sweep);
    arcs.push({
      d: arcPath(cx, cy, rOuter, rInner, start, end),
      color: item.color,
      label: item.label,
      value: item.value,
    });
    cursor = end;
  });

  return (
    <div className="col" style={{ gap: 12, alignItems: 'center' }}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={ariaLabel}>
          <defs>
            <filter id={`donut-shadow-${gradientId}`} x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="1" stdDeviation="2" floodOpacity="0.35" />
            </filter>
          </defs>
          <circle
            cx={cx}
            cy={cy}
            r={(rOuter + rInner) / 2}
            fill="none"
            style={{ stroke: 'var(--bg-3)' }}
            strokeWidth={thickness}
          />
          <g filter={`url(#donut-shadow-${gradientId})`}>
            {arcs.map((arc) => (
              <path key={arc.label} d={arc.d} style={{ fill: arc.color }} />
            ))}
          </g>
        </svg>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'grid',
            placeItems: 'center',
            textAlign: 'center',
          }}
        >
          <div>
            <div className="mono" style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em' }}>
              {centerValue ?? fmtInt(total)}
            </div>
            <div className="fs-11 text-3">{centerLabel ?? '样本总数'}</div>
          </div>
        </div>
      </div>

      <ul style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
        {data.map((item) => {
          const pct = total > 0 ? (item.value / total) * 100 : 0;
          return (
            <li key={item.label} className="row-between fs-12">
              <span className="row" style={{ gap: 6 }}>
                <i className="legend-dot" style={{ background: item.color }} aria-hidden="true" />
                <span className="text-2">{item.label}</span>
              </span>
              <span className="row" style={{ gap: 8 }}>
                <span className="mono">{fmtInt(item.value)}</span>
                <span className="mono text-3" style={{ minWidth: 46, textAlign: 'right' }}>
                  {pct.toFixed(1)}%
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
