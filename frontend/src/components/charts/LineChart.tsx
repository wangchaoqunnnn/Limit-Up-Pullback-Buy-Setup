import { useId, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { useElementWidth } from '../../hooks/useApi';
import { Skeleton } from '../ui/Skeleton';
import { fmtNum } from '../../utils/format';

export interface LinePoint {
  label: string;
  value: number;
}

export interface LineChartProps {
  data: LinePoint[];
  height?: number;
  /** 折线颜色（CSS 变量或颜色值） */
  tone?: string;
  area?: boolean;
  /** 参考基准线（如净值起点 100） */
  baseline?: number | null;
  baselineLabel?: string;
  valueFormat?: (value: number) => string;
  xFormat?: (label: string) => string;
  seriesName?: string;
  loading?: boolean;
  ariaLabel?: string;
  /** y 轴刻度数量 */
  yTicks?: number;
}

/** 手写 SVG 折线图：渐变区域填充 + 极淡网格 + hover 十字光标与数据浮层 */
export function LineChart({
  data,
  height = 250,
  tone = 'var(--brand)',
  area = true,
  baseline = null,
  baselineLabel = '基准',
  valueFormat = (v) => fmtNum(v, 2),
  xFormat,
  seriesName = '数值',
  loading = false,
  ariaLabel = '折线图',
  yTicks = 5,
}: LineChartProps) {
  const gradientId = useId().replace(/[:]/g, '');
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  if (loading) {
    // 仍渲染外层容器，保证宽度观察器在首帧就能挂载
    return (
      <div className="chart" ref={wrapRef}>
        <Skeleton variant="rect" height={height} />
      </div>
    );
  }

  const items = Array.isArray(data)
    ? data.filter((d) => d && Number.isFinite(d.value)).map((d) => ({ label: String(d.label ?? ''), value: d.value }))
    : [];

  const w = width || 640;
  const padT = 16;
  const padB = 24;
  const padL = 8;
  const padR = 56;
  const plotW = Math.max(10, w - padL - padR);
  const plotH = Math.max(10, height - padT - padB);

  if (items.length < 2) {
    return (
      <div className="chart-empty" style={{ height }}>
        暂无可绘制的曲线数据
      </div>
    );
  }

  const values = items.map((d) => d.value);
  if (baseline !== null && Number.isFinite(baseline)) values.push(baseline);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin || Math.max(Math.abs(rawMax), 1) * 0.05;
  const yMin = rawMin - span * 0.08;
  const yMax = rawMax + span * 0.08;

  const xOf = (i: number) => padL + (i / (items.length - 1)) * plotW;
  const yOf = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const linePath = items.map((d, i) => `${i === 0 ? 'M' : 'L'}${xOf(i).toFixed(2)},${yOf(d.value).toFixed(2)}`).join(' ');
  const areaPath = `${linePath} L${xOf(items.length - 1).toFixed(2)},${padT + plotH} L${xOf(0).toFixed(2)},${padT + plotH} Z`;

  const tickValues = Array.from({ length: yTicks }, (_, i) => yMax - ((yMax - yMin) * i) / (yTicks - 1));
  const xTickCount = Math.max(2, Math.min(7, Math.floor(plotW / 96)));
  const xTickIdx = Array.from({ length: xTickCount }, (_, i) =>
    Math.round((i / (xTickCount - 1)) * (items.length - 1)),
  );

  const onMove = (event: ReactMouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const rel = event.clientX - rect.left;
    const idx = Math.round(((rel - padL) / plotW) * (items.length - 1));
    setHover(Math.max(0, Math.min(items.length - 1, idx)));
  };

  const hovered = hover !== null ? items[hover] : null;

  return (
    <div className="chart" ref={wrapRef}>
      <svg
        className="chart-svg kline-cursor"
        width={w}
        height={height}
        viewBox={`0 0 ${w} ${height}`}
        role="img"
        aria-label={ariaLabel}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={`line-area-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: tone, stopOpacity: 0.34 }} />
            <stop offset="100%" style={{ stopColor: tone, stopOpacity: 0 }} />
          </linearGradient>
          <linearGradient id={`line-stroke-${gradientId}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" style={{ stopColor: tone, stopOpacity: 0.75 }} />
            <stop offset="100%" style={{ stopColor: tone, stopOpacity: 1 }} />
          </linearGradient>
        </defs>

        <g className="chart-grid">
          {tickValues.map((v, i) => (
            <line key={i} x1={padL} x2={padL + plotW} y1={yOf(v)} y2={yOf(v)} />
          ))}
        </g>

        <g>
          {tickValues.map((v, i) => (
            <text key={i} x={padL + plotW + 6} y={yOf(v)} dominantBaseline="middle" className="chart-axis-text">
              {valueFormat(v)}
            </text>
          ))}
        </g>

        {baseline !== null && Number.isFinite(baseline) && (
          <g>
            <line
              x1={padL}
              x2={padL + plotW}
              y1={yOf(baseline)}
              y2={yOf(baseline)}
              style={{ stroke: 'var(--gold)', strokeWidth: 1, strokeDasharray: '4 4', opacity: 0.75 }}
            />
            <text
              x={padL + 4}
              y={yOf(baseline) - 4}
              className="chart-axis-text"
              style={{ fill: 'var(--gold)' }}
            >
              {baselineLabel}
            </text>
          </g>
        )}

        {area && <path d={areaPath} fill={`url(#line-area-${gradientId})`} />}
        <path
          d={linePath}
          fill="none"
          stroke={`url(#line-stroke-${gradientId})`}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ transition: 'd 200ms cubic-bezier(0.4,0,0.2,1)' }}
        />

        {/* x 轴标签 */}
        <g>
          {xTickIdx.map((idx) => (
            <text
              key={idx}
              x={xOf(idx)}
              y={height - 7}
              textAnchor={idx === 0 ? 'start' : idx === items.length - 1 ? 'end' : 'middle'}
              className="chart-axis-text"
            >
              {xFormat ? xFormat(items[idx].label) : items[idx].label}
            </text>
          ))}
        </g>

        {/* 十字光标 */}
        {hovered && hover !== null && (
          <g>
            <line
              x1={xOf(hover)}
              x2={xOf(hover)}
              y1={padT}
              y2={padT + plotH}
              style={{ stroke: 'var(--text-2)', strokeWidth: 1, strokeDasharray: '3 3' }}
            />
            <line
              x1={padL}
              x2={padL + plotW}
              y1={yOf(hovered.value)}
              y2={yOf(hovered.value)}
              style={{ stroke: 'var(--text-2)', strokeWidth: 1, strokeDasharray: '3 3' }}
            />
            <circle
              cx={xOf(hover)}
              cy={yOf(hovered.value)}
              r={3.6}
              style={{ fill: 'var(--surface)', stroke: tone }}
              strokeWidth={2}
            />
          </g>
        )}
      </svg>

      {hovered && hover !== null && (
        <div
          className="chart-tip"
          style={{
            left: Math.min(Math.max(xOf(hover) - 70, 4), Math.max(4, w - 160)),
            top: Math.max(4, yOf(hovered.value) - 68),
          }}
        >
          <div className="chart-tip-title">
            <span>{hovered.label}</span>
          </div>
          <div className="chart-tip-row">
            <span className="chart-tip-k">{seriesName}</span>
            <span className="chart-tip-v">{valueFormat(hovered.value)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
