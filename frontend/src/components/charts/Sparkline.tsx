import { useEffect, useId, useRef, useState } from 'react';
import clsx from 'clsx';

export type SparkTone = 'auto' | 'up' | 'down' | 'brand' | 'gold' | 'teal' | 'flat';

export interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  tone?: SparkTone;
  area?: boolean;
  strokeWidth?: number;
  /** 以首值为基准线（判断整体涨跌） */
  baseline?: boolean;
  /** 跟随容器宽度自适应（默认开启，避免定宽 SVG 只画到容器一半） */
  responsive?: boolean;
  className?: string;
  ariaLabel?: string;
}

function toneColor(tone: SparkTone): string {
  switch (tone) {
    case 'up':
      return 'var(--up)';
    case 'down':
      return 'var(--down)';
    case 'brand':
      return 'var(--brand)';
    case 'gold':
      return 'var(--gold)';
    case 'teal':
      return 'var(--teal)';
    case 'flat':
      return 'var(--flat)';
    default:
      return 'var(--brand)';
  }
}

/**
 * 迷你走势图：纯 SVG，无坐标轴，用于表格行与指数卡片。
 * 宽度默认由 ResizeObserver 测量容器后按真实像素渲染，
 * 保证序列从头画到尾、面积填充不会在中间被竖直切断。
 */
export function Sparkline({
  data,
  width = 96,
  height = 28,
  tone = 'auto',
  area = true,
  strokeWidth = 1.4,
  baseline = false,
  responsive = true,
  className,
  ariaLabel = '走势迷你图',
}: SparklineProps) {
  const gradientId = useId().replace(/[:]/g, '');
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const [measured, setMeasured] = useState<number | null>(null);

  useEffect(() => {
    if (!responsive) return;
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => {
      const next = Math.round(el.clientWidth);
      if (next > 0) setMeasured((prev) => (prev === next ? prev : next));
    };
    apply();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', apply);
      return () => window.removeEventListener('resize', apply);
    }
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [responsive]);

  const clean = Array.isArray(data) ? data.filter((v) => typeof v === 'number' && Number.isFinite(v)) : [];
  const svgW = Math.max(24, responsive ? measured ?? width : width);

  return (
    <span
      ref={wrapRef}
      className={clsx('spark-wrap', className)}
      style={{ display: 'block', width: '100%', minWidth: width, height }}
    >
      {clean.length < 2 ? (
        <span
          className="text-3 fs-11"
          style={{ display: 'block', lineHeight: `${height}px`, textAlign: 'center' }}
        >
          —
        </span>
      ) : (
        <SparkSvg
          values={clean}
          svgW={svgW}
          height={height}
          tone={tone}
          area={area}
          strokeWidth={strokeWidth}
          baseline={baseline}
          gradientId={gradientId}
          ariaLabel={ariaLabel}
        />
      )}
    </span>
  );
}

interface SparkSvgProps {
  values: number[];
  svgW: number;
  height: number;
  tone: SparkTone;
  area: boolean;
  strokeWidth: number;
  baseline: boolean;
  gradientId: string;
  ariaLabel: string;
}

function SparkSvg({
  values,
  svgW,
  height,
  tone,
  area,
  strokeWidth,
  baseline,
  gradientId,
  ariaLabel,
}: SparkSvgProps) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.max(Math.abs(max), 1) * 0.02 || 1;
  const padY = strokeWidth + 1;
  const usableH = Math.max(1, height - padY * 2);

  const x = (i: number) => (i / (values.length - 1)) * svgW;
  const y = (v: number) => padY + (1 - (v - min) / span) * usableH;

  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  const areaPath = `${line} L${svgW.toFixed(2)},${height} L0,${height} Z`;

  const resolved: SparkTone = tone === 'auto' ? (values[values.length - 1] >= values[0] ? 'up' : 'down') : tone;
  const color = toneColor(resolved);

  return (
    <svg
      width={svgW}
      height={height}
      viewBox={`0 0 ${svgW} ${height}`}
      role="img"
      aria-label={ariaLabel}
      style={{ display: 'block', width: '100%' }}
    >
      <defs>
        <linearGradient id={`spark-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" style={{ stopColor: color, stopOpacity: 0.32 }} />
          <stop offset="100%" style={{ stopColor: color, stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      {baseline && (
        <line
          x1={0}
          x2={svgW}
          y1={y(values[0])}
          y2={y(values[0])}
          style={{ stroke: 'var(--border-strong)', strokeWidth: 1, strokeDasharray: '2 3' }}
        />
      )}
      {area && <path d={areaPath} fill={`url(#spark-${gradientId})`} />}
      <path
        d={line}
        fill="none"
        style={{ stroke: color, strokeWidth }}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={1.8} style={{ fill: color }} />
    </svg>
  );
}
