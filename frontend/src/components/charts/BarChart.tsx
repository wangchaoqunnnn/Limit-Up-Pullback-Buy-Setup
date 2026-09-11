import { useId, useState } from 'react';
import { Skeleton } from '../ui/Skeleton';
import { useElementWidth } from '../../hooks/useApi';
import { fmtNum } from '../../utils/format';

export interface BarDatum {
  label: string;
  value: number;
  color?: string;
  hint?: string;
}

export interface BarChartProps {
  data: BarDatum[];
  height?: number;
  valueFormat?: (value: number) => string;
  ariaLabel?: string;
  loading?: boolean;
  /** y 轴最大值（不传则按数据自适应） */
  maxValue?: number;
  barColor?: string;
  /** 每根柱子下方标签的最大显示数量（超出则间隔显示） */
  maxLabels?: number;
}

/** 手写 SVG 柱状图（信号通过率、板块涨停家数） */
export function BarChart({
  data,
  height = 190,
  valueFormat = (v) => fmtNum(v, 0),
  ariaLabel = '柱状图',
  loading = false,
  maxValue,
  barColor = 'var(--brand)',
  maxLabels = 8,
}: BarChartProps) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ index: number; x: number; y: number } | null>(null);
  const gradientId = useId().replace(/[:]/g, '');

  if (loading) {
    // 仍渲染外层容器，保证宽度观察器在首帧就能挂载
    return (
      <div className="chart" ref={wrapRef}>
        <Skeleton variant="rect" height={height} />
      </div>
    );
  }

  const items = Array.isArray(data) ? data.filter((d) => d && Number.isFinite(d.value)) : [];
  const w = width || 560;
  const padT = 24;
  const padB = 26;
  const padL = 6;
  const padR = 48;
  const plotW = Math.max(10, w - padL - padR);
  const plotH = Math.max(10, height - padT - padB);

  const dataMax = items.length ? Math.max(...items.map((d) => d.value), 0) : 0;
  // 指定上限时不得再放大（否则 0~100 的通过率轴会出现 112% 这种不存在的刻度）
  const yMax = maxValue !== undefined ? Math.max(maxValue, dataMax, 1) : Math.max(dataMax * 1.12, 1);

  const step = items.length ? plotW / items.length : plotW;
  const barW = Math.max(6, Math.min(step * 0.56, 46));

  const yOf = (v: number) => padT + (1 - v / yMax) * plotH;

  const gridLines = [0, 0.25, 0.5, 0.75, 1];
  const labelStride = Math.max(1, Math.ceil(items.length / maxLabels));

  return (
    <div className="chart" ref={wrapRef}>
      <svg
        className="chart-svg kline-cursor"
        width={w}
        height={height}
        viewBox={`0 0 ${w} ${height}`}
        role="img"
        aria-label={ariaLabel}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={`bar-grad-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: barColor, stopOpacity: 0.95 }} />
            <stop offset="100%" style={{ stopColor: barColor, stopOpacity: 0.35 }} />
          </linearGradient>
        </defs>

        <g className="chart-grid">
          {gridLines.map((t) => {
            const y = padT + t * plotH;
            return <line key={t} x1={padL} x2={padL + plotW} y1={y} y2={y} />;
          })}
        </g>
        <g>
          {gridLines.map((t) => (
            <text
              key={t}
              x={padL + plotW + 6}
              y={padT + t * plotH}
              dominantBaseline="middle"
              className="chart-axis-text"
            >
              {valueFormat(yMax * (1 - t))}
            </text>
          ))}
        </g>

        {items.map((d, i) => {
          const x = padL + step * i + (step - barW) / 2;
          const y = yOf(Math.max(0, d.value));
          const h = Math.max(1.5, padT + plotH - y);
          const color = d.color ?? `url(#bar-grad-${gradientId})`;
          const labelY = Math.max(padT - 8, y - 6);
          return (
            <g key={`${d.label}-${i}`}>
              <rect
                x={x}
                y={y}
                width={barW}
                height={h}
                rx={3}
                style={{ fill: color, transition: 'all 200ms cubic-bezier(0.4,0,0.2,1)' }}
                opacity={hover && hover.index !== i ? 0.55 : 1}
              />
              <text
                x={x + barW / 2}
                y={labelY}
                textAnchor="middle"
                className="chart-axis-text"
                style={{ fill: 'var(--text-2)', fontWeight: 600 }}
              >
                {valueFormat(d.value)}
              </text>
              {hover?.index === i && (
                <rect
                  x={x}
                  y={y}
                  width={barW}
                  height={h}
                  rx={3}
                  fill="none"
                  style={{ stroke: 'var(--text-1)', strokeWidth: 1, opacity: 0.6 }}
                />
              )}
              <rect
                x={padL + step * i}
                y={padT}
                width={step}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover({ index: i, x: padL + step * i + step / 2, y })}
              />
            </g>
          );
        })}

        <g>
          {items.map((d, i) =>
            i % labelStride === 0 ? (
              <text
                key={`t-${d.label}-${i}`}
                x={padL + step * i + step / 2}
                y={height - 8}
                textAnchor="middle"
                className="chart-axis-text"
                style={{ fontSize: 11 }}
              >
                {d.label.length > 7 ? `${d.label.slice(0, 6)}…` : d.label}
              </text>
            ) : null,
          )}
        </g>
      </svg>

      {hover && items[hover.index] && (
        <div
          className="chart-tip"
          style={{
            left: Math.min(Math.max(hover.x - 70, 4), Math.max(4, w - 156)),
            top: Math.max(4, hover.y - 62),
          }}
        >
          <div className="chart-tip-title">{items[hover.index].label}</div>
          <div className="chart-tip-row">
            <span className="chart-tip-k">数值</span>
            <span className="chart-tip-v">{valueFormat(items[hover.index].value)}</span>
          </div>
          {items[hover.index].hint && (
            <div className="chart-tip-row">
              <span className="chart-tip-k">备注</span>
              <span className="chart-tip-v">{items[hover.index].hint}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
