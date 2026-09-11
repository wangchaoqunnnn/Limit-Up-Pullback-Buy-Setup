import { useId } from 'react';
import clsx from 'clsx';
import { clamp } from '../../utils/format';

export interface GaugeChartProps {
  /** 0 ~ 100 */
  score: number | null;
  level?: string | null;
  size?: number;
  label?: string;
  ariaLabel?: string;
}

/** 情绪档位锚点：刻度数字之外再给可读的中文语义锚点 */
const LEVEL_ANCHORS: { text: string; at: number }[] = [
  { text: '冰点', at: 0 },
  { text: '偏冷', at: 25 },
  { text: '中性', at: 50 },
  { text: '偏暖', at: 75 },
  { text: '过热', at: 100 },
];

const TICKS = [0, 25, 50, 75, 100];

function polar(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

/** 市场情绪仪表盘：210° 弧 + 冷→热发散渐变 + 实心指针 + 0~100 刻度与档位锚点 */
export function GaugeChart({ score, level, size = 240, label = '市场情绪', ariaLabel }: GaugeChartProps) {
  const gradientId = useId().replace(/[:]/g, '');
  const value = score === null || !Number.isFinite(score) ? 0 : clamp(score, 0, 100);

  const START = 162;
  const END = 378;
  const angleAt = (t: number) => START + (END - START) * clamp(t, 0, 1);

  const r = size / 2 - 34;
  const cx = size / 2;
  // 留出外侧刻度文字空间；同时保证弧线两端与刻度数字都不被视口裁切
  const cy = r + 30;
  const height = Math.round(cy + (r + 24) * Math.sin((START * Math.PI) / 180) + 8);

  const arc = (t0: number, t1: number, radius: number) => {
    const a0 = angleAt(t0);
    const a1 = angleAt(t1);
    const p0 = polar(cx, cy, radius, a0);
    const p1 = polar(cx, cy, radius, a1);
    const large = a1 - a0 > 180 ? 1 : 0;
    return `M${p0.x.toFixed(2)},${p0.y.toFixed(2)} A${radius},${radius} 0 ${large} 1 ${p1.x.toFixed(2)},${p1.y.toFixed(2)}`;
  };

  // 实心三角指针：尖端真正落在当前值对应的半径上
  const needleAngle = angleAt(value / 100);
  const rad = (needleAngle * Math.PI) / 180;
  const dx = Math.cos(rad);
  const dy = Math.sin(rad);
  const tip = { x: cx + dx * (r - 3), y: cy + dy * (r - 3) };
  const back = { x: cx - dx * 13, y: cy - dy * 13 };
  const nx = -dy;
  const ny = dx;
  const half = 5;
  const needlePoints = [
    `${tip.x.toFixed(2)},${tip.y.toFixed(2)}`,
    `${(back.x + nx * half).toFixed(2)},${(back.y + ny * half).toFixed(2)}`,
    `${(back.x - nx * half).toFixed(2)},${(back.y - ny * half).toFixed(2)}`,
  ].join(' ');

  const activeIndex = value >= 87.5 ? 4 : value >= 62.5 ? 3 : value >= 37.5 ? 2 : value >= 12.5 ? 1 : 0;

  return (
    <div className="gauge-wrap">
      <svg
        viewBox={`0 0 ${size} ${height}`}
        width={size}
        height={height}
        style={{ width: '100%', maxWidth: size, height: 'auto' }}
        role="img"
        aria-label={ariaLabel ?? `${label}评分 ${value.toFixed(1)}，${level ?? ''}`}
      >
        <defs>
          {/* 冷 → 热发散量表：冰点=品牌蓝，中性=琥珀，过热=危险红（与「过热」徽章同色） */}
          <linearGradient id={`gauge-${gradientId}`} x1="0" y1="0.2" x2="1" y2="0.2">
            <stop offset="0%" style={{ stopColor: 'var(--brand)' }} />
            <stop offset="50%" style={{ stopColor: 'var(--gold)' }} />
            <stop offset="100%" style={{ stopColor: 'var(--danger)' }} />
          </linearGradient>
        </defs>

        {/* 底层轨道 */}
        <path d={arc(0, 1, r)} fill="none" style={{ stroke: 'var(--bg-3)' }} strokeWidth={14} strokeLinecap="round" />
        {/* 数值弧 */}
        {value > 0.5 && (
          <path
            d={arc(0, value / 100, r)}
            fill="none"
            stroke={`url(#gauge-${gradientId})`}
            strokeWidth={14}
            strokeLinecap="round"
            style={{ transition: 'all 250ms cubic-bezier(0.4,0,0.2,1)' }}
          />
        )}

        {/* 刻度线 + 刻度数字（11px，对比度 ≥ 4.5:1） */}
        {TICKS.map((tick) => {
          const angle = angleAt(tick / 100);
          const inner = polar(cx, cy, r + 9, angle);
          const outer = polar(cx, cy, r + 14, angle);
          const textPos = polar(cx, cy, r + 24, angle);
          return (
            <g key={tick}>
              <line
                x1={inner.x}
                y1={inner.y}
                x2={outer.x}
                y2={outer.y}
                style={{ stroke: 'var(--border-strong)' }}
                strokeWidth={1}
              />
              <text
                x={textPos.x}
                y={textPos.y}
                textAnchor="middle"
                dominantBaseline="middle"
                style={{ fill: 'var(--text-2)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
              >
                {tick}
              </text>
            </g>
          );
        })}

        {/* 指针 */}
        <polygon points={needlePoints} style={{ fill: 'var(--text-1)' }} />
        <circle cx={cx} cy={cy} r={5.5} style={{ fill: 'var(--surface)', stroke: 'var(--text-1)' }} strokeWidth={2} />

        {/* 中心数值
            注意：数值基线必须明显高于指针枢纽圆心（cy），否则 26px 的数字
            下缘会与半径 5.5px 的白色枢纽圆环、以及指针杆发生重叠，
            造成数字被白斑吞掉（小数点消失、「3」的下碗被填白）。 */}
        <text
          x={cx}
          y={cy - 22}
          textAnchor="middle"
          style={{
            fill: 'var(--text-1)',
            fontSize: 26,
            fontWeight: 700,
            letterSpacing: '-0.02em',
            fontFamily: 'var(--font-mono)',
            paintOrder: 'stroke',
            stroke: 'var(--surface)',
            strokeWidth: 3,
          }}
        >
          {value.toFixed(1)}
        </text>
        <text x={cx} y={cy + 30} textAnchor="middle" style={{ fill: 'var(--text-2)', fontSize: 12 }}>
          {label}
        </text>
      </svg>

      <div className="gauge-scale" aria-hidden="true">
        {LEVEL_ANCHORS.map((anchor, index) => (
          <span key={anchor.text} className={clsx('gauge-scale-item', index === activeIndex && 'on')}>
            {anchor.text}
          </span>
        ))}
      </div>
    </div>
  );
}
