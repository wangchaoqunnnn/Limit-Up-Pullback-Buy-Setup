import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import clsx from 'clsx';
import type { Candle } from '../../api/types';
import { useElementWidth } from '../../hooks/useApi';
import { clamp, fmtAmount, fmtDateShort, fmtPct, fmtPrice, fmtTimes, fmtVolume, isNum } from '../../utils/format';
import { Skeleton } from '../ui/Skeleton';

export interface SupportLine {
  price: number;
  label: string;
  tone?: 'gold' | 'brand' | 'text';
}

export interface KLineChartProps {
  candles: Candle[];
  height?: number;
  /** 关键支撑位横线（涨停开盘价 / 实体半分位） */
  supportLines?: SupportLine[];
  /** 买入区间色带 */
  buyZone?: { low: number | null; high: number | null } | null;
  loading?: boolean;
  ariaLabel?: string;
  /** 默认显示最近 N 根 */
  defaultVisible?: number;
}

interface MAValues {
  ma5: (number | null)[];
  ma10: (number | null)[];
  ma20: (number | null)[];
}

function computeMA(closes: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  let sum = 0;
  for (let i = 0; i < closes.length; i += 1) {
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/**
 * 专业 K 线图（纯 SVG 手写）：
 * 蜡烛实体 + 影线、成交量副图（共享 x 轴）、MA5/10/20、涨停日金色标记、
 * 支撑位水平虚线标注、买入区间色带、hover 十字光标 + 数据浮层、拖动平移、滚轮缩放。
 */
export function KLineChart({
  candles,
  height = 460,
  supportLines = [],
  buyZone = null,
  loading = false,
  ariaLabel = 'K线图',
  defaultVisible = 90,
}: KLineChartProps) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState<{ end: number; count: number }>({ end: 0, count: 0 });
  const [hover, setHover] = useState<number | null>(null);
  const dragRef = useRef<{ startX: number; startEnd: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);

  const total = candles.length;

  // 数据变化时重置可视窗口
  useEffect(() => {
    if (total === 0) {
      setView({ end: 0, count: 0 });
      return;
    }
    setView({ end: total, count: Math.min(defaultVisible, total) });
  }, [total, defaultVisible]);

  const ma: MAValues = useMemo(() => {
    const closes = candles.map((c) => c.close);
    return {
      ma5: computeMA(closes, 5),
      ma10: computeMA(closes, 10),
      ma20: computeMA(closes, 20),
    };
  }, [candles]);

  // 滚轮缩放：原生非被动监听，避免 React 合成事件无法 preventDefault
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || total === 0) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      setView((prev) => {
        if (prev.count === 0) return prev;
        const rect = svg.getBoundingClientRect();
        const padL = 8;
        const padR = 58;
        const plotW = Math.max(10, rect.width - padL - padR);
        const barW = plotW / prev.count;
        const relX = clamp(event.clientX - rect.left - padL, 0, plotW);
        const anchor = prev.end - prev.count + relX / barW;
        const factor = event.deltaY > 0 ? 1.15 : 1 / 1.15;
        const nextCount = Math.round(clamp(prev.count * factor, 20, total));
        if (nextCount === prev.count) return prev;
        let nextEnd = Math.round(anchor + (prev.end - anchor) * (nextCount / prev.count));
        nextEnd = Math.round(clamp(nextEnd, nextCount, total));
        return { end: nextEnd, count: nextCount };
      });
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [total]);

  if (loading) {
    // 仍渲染外层容器，保证宽度观察器与滚轮监听在首帧就能挂载
    return (
      <div className="chart" ref={wrapRef}>
        <Skeleton variant="rect" height={height} />
      </div>
    );
  }

  if (total === 0) {
    return (
      <div className="chart-empty" style={{ height }}>
        暂无 K 线数据
      </div>
    );
  }

  const w = width || 760;
  const padL = 8;
  const padR = 58;
  // 顶部留出图例 / 区间切换按钮的底板高度，避免与最高价 K 线互相压盖
  const padT = 30;
  const padB = 22;
  const gap = 14;
  const usableH = height - padT - padB - gap;
  const mainH = Math.max(60, usableH * 0.72);
  const volH = Math.max(36, usableH * 0.28);
  const volTop = padT + mainH + gap;
  const plotW = Math.max(60, w - padL - padR);

  const count = view.count > 0 ? view.count : Math.min(defaultVisible, total);
  const end = view.end > 0 ? view.end : total;
  const start = Math.max(0, end - count);
  const visible = candles.slice(start, end);
  const barW = plotW / Math.max(1, count);
  const bodyW = Math.max(1.2, Math.min(barW * 0.66, 18));

  const xOfIndex = (absIndex: number) => padL + (absIndex - start) * barW + barW / 2;

  // ---- 价格域 ----
  const lows = visible.map((c) => c.low).filter(isNum);
  const highs = visible.map((c) => c.high).filter(isNum);
  let min = lows.length ? Math.min(...lows) : 0;
  let max = highs.length ? Math.max(...highs) : 1;
  const maVals: number[] = [];
  for (let i = start; i < end; i += 1) {
    [ma.ma5[i], ma.ma10[i], ma.ma20[i]].forEach((v) => {
      if (isNum(v)) maVals.push(v);
    });
  }
  if (maVals.length) {
    min = Math.min(min, ...maVals);
    max = Math.max(max, ...maVals);
  }
  if (buyZone && isNum(buyZone.low)) min = Math.min(min, buyZone.low);
  if (buyZone && isNum(buyZone.high)) max = Math.max(max, buyZone.high);

  const rawRange = max - min || Math.max(max * 0.02, 0.01);
  const tolerance = rawRange * 0.3;
  const drawnSupports = supportLines.filter((s) => isNum(s.price) && s.price >= min - tolerance && s.price <= max + tolerance);
  drawnSupports.forEach((s) => {
    min = Math.min(min, s.price);
    max = Math.max(max, s.price);
  });

  const padRange = (max - min) * 0.06 || Math.max(max * 0.01, 0.01);
  const yMin = min - padRange;
  const yMax = max + padRange;
  const yOf = (price: number) => padT + (1 - (price - yMin) / (yMax - yMin)) * mainH;

  // ---- 量能 ----
  const volMax = Math.max(...visible.map((c) => (isNum(c.volume) ? c.volume : 0)), 1);
  const vyOf = (v: number) => volTop + volH - (v / volMax) * volH;

  // ---- 网格与刻度 ----
  const priceTicks = Array.from({ length: 5 }, (_, i) => yMax - ((yMax - yMin) * i) / 4);
  const xTickCount = Math.max(2, Math.min(7, Math.floor(plotW / 90)));
  const xTicks = Array.from({ length: xTickCount }, (_, i) =>
    Math.round(start + (i / (xTickCount - 1)) * Math.max(0, visible.length - 1)),
  );

  const colorOf = (c: Candle) => {
    const base = isNum(c.preClose) && c.preClose > 0 ? c.preClose : c.open;
    if (c.close > base) return 'var(--up)';
    if (c.close < base) return 'var(--down)';
    return 'var(--flat)';
  };

  const maPath = (series: (number | null)[]) => {
    let d = '';
    let open = false;
    for (let i = start; i < end; i += 1) {
      const v = series[i];
      if (!isNum(v)) {
        open = false;
        continue;
      }
      const x = xOfIndex(i);
      const y = yOf(v);
      d += `${open ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
      open = true;
    }
    return d.trim();
  };

  const lastCandle = candles[total - 1];
  const lastCloseInView = end === total;
  const lastCloseY = yOf(lastCandle.close);

  // ---- 买入区间几何 ----
  const zoneLow = buyZone && isNum(buyZone.low) ? buyZone.low : null;
  const zoneHigh = buyZone && isNum(buyZone.high) ? buyZone.high : null;
  const zoneMid = zoneLow !== null && zoneHigh !== null ? (zoneLow + zoneHigh) / 2 : null;
  const zoneSpan = zoneLow !== null && zoneHigh !== null ? Math.abs(zoneHigh - zoneLow) : 0;
  // 区间过窄（或上下沿重合）时矩形会退化成两根竖直实心条，此时只画一条水平虚线
  const zoneIsBand = zoneMid !== null && zoneMid > 0 && zoneSpan / zoneMid >= 0.003;

  // ---- 支撑位文字错位排布（避免同一水平线重叠） ----
  const supportLabels = drawnSupports
    .map((line) => ({
      text: `${line.label} ${fmtPrice(line.price)}`,
      color: line.tone === 'brand' ? 'var(--brand)' : line.tone === 'text' ? 'var(--text-2)' : 'var(--gold)',
      rawY: yOf(line.price),
    }))
    .sort((a, b) => a.rawY - b.rawY)
    .reduce<{ text: string; color: string; y: number }[]>((acc, item) => {
      const prev = acc[acc.length - 1];
      const y = prev ? Math.max(item.rawY, prev.y + 15) : item.rawY;
      acc.push({ text: item.text, color: item.color, y: Math.min(y, padT + mainH - 4) });
      return acc;
    }, []);

  // ---- 交互 ----
  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    dragRef.current = { startX: event.clientX, startEnd: end, moved: false };
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const relX = event.clientX - rect.left;

    const drag = dragRef.current;
    if (drag) {
      const deltaBars = Math.round((drag.startX - event.clientX) / Math.max(barW, 0.5));
      if (Math.abs(drag.startX - event.clientX) > 3) drag.moved = true;
      const nextEnd = Math.round(clamp(drag.startEnd + deltaBars, count, total));
      setView((prev) => (prev.end === nextEnd ? prev : { ...prev, end: nextEnd }));
      return;
    }

    if (relX < padL || relX > padL + plotW) {
      setHover(null);
      return;
    }
    const idx = start + Math.floor((relX - padL) / barW);
    setHover(clamp(idx, 0, total - 1));
  };

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    return drag?.moved ?? false;
  };

  const hoveredCandle = hover !== null ? candles[hover] : null;
  const hoverInView = hover !== null && hover >= start && hover < end;

  const setRange = (bars: number) => {
    const nextCount = Math.round(clamp(bars, 20, total));
    setView({ end: total, count: nextCount });
  };

  return (
    <div className="chart" ref={wrapRef}>
      <div className="kline-legend">
        <span className="row" style={{ gap: 6 }}>
          <span className="legend-item" style={{ color: 'var(--gold)' }}>
            <i className="legend-swatch" style={{ background: 'currentColor' }} />
            MA5
          </span>
          <span className="legend-item" style={{ color: 'var(--brand)' }}>
            <i className="legend-swatch" style={{ background: 'currentColor' }} />
            MA10
          </span>
          <span className="legend-item" style={{ color: 'var(--violet)' }}>
            <i className="legend-swatch" style={{ background: 'currentColor' }} />
            MA20
          </span>
        </span>
      </div>

      <div className="kline-range">
        {[
          { label: '60日', bars: 60 },
          { label: '120日', bars: 120 },
          { label: '全部', bars: total },
        ].map((item) => (
          <button
            key={item.label}
            type="button"
            className={clsx('btn', 'btn-sm', 'btn-ghost')}
            style={{ height: 20, padding: '0 6px', fontSize: 11 }}
            onClick={() => setRange(item.bars)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <svg
        ref={svgRef}
        className={clsx('chart-svg', dragging ? 'kline-panning' : 'kline-cursor')}
        width={w}
        height={height}
        viewBox={`0 0 ${w} ${height}`}
        role="img"
        aria-label={ariaLabel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={(e) => {
          endDrag(e);
          setHover(null);
        }}
      >
        <defs>
          <clipPath id="kline-main-clip">
            <rect x={padL} y={padT} width={plotW} height={mainH} />
          </clipPath>
          <linearGradient id="kline-vol-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: 'var(--brand)', stopOpacity: 0.55 }} />
            <stop offset="100%" style={{ stopColor: 'var(--brand)', stopOpacity: 0.12 }} />
          </linearGradient>
        </defs>

        {/* 网格：仅保留横向价格格线 + 底部短刻度，避免全高竖线被误读为数据条 */}
        <g className="chart-grid">
          {priceTicks.map((p, i) => (
            <line key={`h${i}`} x1={padL} x2={padL + plotW} y1={yOf(p)} y2={yOf(p)} />
          ))}
          {xTicks.map((idx) => (
            <line
              key={`v${idx}`}
              x1={xOfIndex(idx)}
              x2={xOfIndex(idx)}
              y1={padT + mainH - 5}
              y2={padT + mainH}
            />
          ))}
          <line x1={padL} x2={padL + plotW} y1={volTop + volH} y2={volTop + volH} />
        </g>

        {/* 右侧价格刻度（与现价标签重合的刻度让位） */}
        <g>
          {priceTicks.map((p, i) => {
            const y = yOf(p);
            if (lastCloseInView && Math.abs(y - lastCloseY) < 13) return null;
            return (
              <text key={`pt${i}`} x={padL + plotW + 6} y={y} dominantBaseline="middle" className="chart-axis-text">
                {fmtPrice(p)}
              </text>
            );
          })}
          <text x={padL + plotW + 6} y={volTop + 8} className="chart-axis-text" style={{ fontSize: 11 }}>
            {fmtVolume(volMax)}
          </text>
        </g>

        {/* x 轴日期 */}
        <g>
          {xTicks.map((idx) => (
            <text
              key={`xt${idx}`}
              x={xOfIndex(idx)}
              y={height - 6}
              textAnchor="middle"
              className="chart-axis-text"
            >
              {fmtDateShort(candles[idx]?.date)}
            </text>
          ))}
        </g>

        {/* 买入区间：宽区间画色带 + 上下虚线；窄区间只画一条虚线，避免退化成竖向实心条 */}
        {zoneLow !== null && zoneHigh !== null && zoneMid !== null && (
          <g clipPath="url(#kline-main-clip)">
            {zoneIsBand && (
              <>
                <rect
                  x={padL}
                  y={Math.min(yOf(zoneHigh), yOf(zoneLow))}
                  width={plotW}
                  height={Math.max(2, Math.abs(yOf(zoneLow) - yOf(zoneHigh)))}
                  style={{ fill: 'var(--brand)', opacity: 0.09 }}
                />
                <line
                  x1={padL}
                  x2={padL + plotW}
                  y1={yOf(zoneHigh)}
                  y2={yOf(zoneHigh)}
                  style={{ stroke: 'var(--brand)', strokeWidth: 1, strokeDasharray: '5 4', opacity: 0.7 }}
                />
                <line
                  x1={padL}
                  x2={padL + plotW}
                  y1={yOf(zoneLow)}
                  y2={yOf(zoneLow)}
                  style={{ stroke: 'var(--brand)', strokeWidth: 1, strokeDasharray: '5 4', opacity: 0.7 }}
                />
              </>
            )}
            {!zoneIsBand && (
              <line
                x1={padL}
                x2={padL + plotW}
                y1={yOf(zoneMid)}
                y2={yOf(zoneMid)}
                style={{ stroke: 'var(--brand)', strokeWidth: 1.2, strokeDasharray: '5 4', opacity: 0.85 }}
              />
            )}
            <text
              className="chart-overlay-text"
              x={padL + 6}
              y={Math.min(yOf(zoneHigh), yOf(zoneLow)) - 5}
              style={{ fill: 'var(--brand)' }}
            >
              买入区间 {fmtPrice(zoneLow)} ~ {fmtPrice(zoneHigh)}
              {zoneIsBand ? '' : '（区间极窄）'}
            </text>
          </g>
        )}

        {/* 支撑位横线 + 错位标签 */}
        <g clipPath="url(#kline-main-clip)">
          {drawnSupports.map((line) => {
            const stroke = line.tone === 'brand' ? 'var(--brand)' : line.tone === 'text' ? 'var(--text-2)' : 'var(--gold)';
            return (
              <line
                key={`${line.label}-${line.price}`}
                x1={padL}
                x2={padL + plotW}
                y1={yOf(line.price)}
                y2={yOf(line.price)}
                style={{ stroke, strokeWidth: 1, strokeDasharray: '6 4', opacity: 0.8 }}
              />
            );
          })}
          {supportLabels.map((item) => (
            <text
              key={item.text}
              className="chart-overlay-text"
              x={padL + plotW - 6}
              y={item.y - 4}
              textAnchor="end"
              style={{ fill: item.color }}
            >
              {item.text}
            </text>
          ))}
        </g>

        {/* K 线蜡烛 */}
        <g clipPath="url(#kline-main-clip)">
          {visible.map((c, i) => {
            const abs = start + i;
            const x = xOfIndex(abs);
            const color = colorOf(c);
            const isUp = color === 'var(--up)';
            const bodyTop = yOf(Math.max(c.open, c.close));
            const bodyBottom = yOf(Math.min(c.open, c.close));
            const bodyH = Math.max(1, bodyBottom - bodyTop);
            return (
              <g key={`${c.date}-${abs}`}>
                <line
                  x1={x}
                  x2={x}
                  y1={yOf(c.high)}
                  y2={yOf(c.low)}
                  style={{ stroke: color, strokeWidth: 1 }}
                />
                <rect
                  x={x - bodyW / 2}
                  y={bodyTop}
                  width={bodyW}
                  height={bodyH}
                  style={{
                    fill: isUp ? color : 'transparent',
                    stroke: color,
                    strokeWidth: 1,
                  }}
                />
                {c.isLimitUp && (
                  <>
                    <rect
                      x={x - bodyW / 2 - 1.5}
                      y={bodyTop - 1.5}
                      width={bodyW + 3}
                      height={bodyH + 3}
                      fill="none"
                      style={{ stroke: 'var(--gold)', strokeWidth: 1.2, opacity: 0.9 }}
                    />
                    <path
                      d={`M${x - 3.4},${yOf(c.low) + 5.5} L${x},${yOf(c.low) + 1.5} L${x + 3.4},${yOf(c.low) + 5.5} Z`}
                      style={{ fill: 'var(--gold)' }}
                    />
                  </>
                )}
              </g>
            );
          })}
        </g>

        {/* 均线 */}
        <g clipPath="url(#kline-main-clip)">
          <path d={maPath(ma.ma5)} fill="none" style={{ stroke: 'var(--gold)', strokeWidth: 1.2, opacity: 0.95 }} />
          <path d={maPath(ma.ma10)} fill="none" style={{ stroke: 'var(--brand)', strokeWidth: 1.2, opacity: 0.95 }} />
          <path d={maPath(ma.ma20)} fill="none" style={{ stroke: 'var(--violet)', strokeWidth: 1.2, opacity: 0.95 }} />
        </g>

        {/* 最新收盘价标记 */}
        {lastCloseInView && visible.length > 0 && (
          <g>
            <line
              x1={padL}
              x2={padL + plotW}
              y1={yOf(lastCandle.close)}
              y2={yOf(lastCandle.close)}
              style={{ stroke: colorOf(lastCandle), strokeWidth: 1, strokeDasharray: '2 3', opacity: 0.8 }}
            />
            <rect
              x={padL + plotW + 2}
              y={yOf(lastCandle.close) - 8}
              width={padR - 6}
              height={16}
              rx={2}
              style={{ fill: colorOf(lastCandle) }}
            />
            <text
              x={padL + plotW + 6}
              y={yOf(lastCandle.close)}
              dominantBaseline="middle"
              className="chart-axis-text"
              style={{ fill: '#fff', fontWeight: 600 }}
            >
              {fmtPrice(lastCandle.close)}
            </text>
          </g>
        )}

        {/* 成交量副图 */}
        <g>
          {visible.map((c, i) => {
            const abs = start + i;
            const x = xOfIndex(abs);
            const v = isNum(c.volume) ? c.volume : 0;
            const y = vyOf(v);
            const color = colorOf(c);
            const isUp = color === 'var(--up)';
            return (
              <rect
                key={`v-${c.date}-${abs}`}
                x={x - bodyW / 2}
                y={y}
                width={bodyW}
                height={Math.max(0.8, volTop + volH - y)}
                style={{
                  fill: c.isLimitUp ? 'var(--gold)' : isUp ? color : 'transparent',
                  stroke: color,
                  strokeWidth: 0.8,
                  opacity: c.isLimitUp ? 0.9 : 0.85,
                }}
              />
            );
          })}
        </g>

        {/* 十字光标 */}
        {hoveredCandle && hoverInView && hover !== null && (
          <g>
            <line
              x1={xOfIndex(hover)}
              x2={xOfIndex(hover)}
              y1={padT}
              y2={volTop + volH}
              style={{ stroke: 'var(--text-2)', strokeWidth: 1, strokeDasharray: '3 3' }}
            />
            <line
              x1={padL}
              x2={padL + plotW}
              y1={yOf(hoveredCandle.close)}
              y2={yOf(hoveredCandle.close)}
              style={{ stroke: 'var(--text-2)', strokeWidth: 1, strokeDasharray: '3 3' }}
            />
            <rect
              x={padL + plotW + 2}
              y={yOf(hoveredCandle.close) - 8}
              width={padR - 6}
              height={16}
              rx={2}
              style={{ fill: 'var(--bg-3)', stroke: 'var(--border-strong)', strokeWidth: 1 }}
            />
            <text
              x={padL + plotW + 6}
              y={yOf(hoveredCandle.close)}
              dominantBaseline="middle"
              className="chart-axis-text"
              style={{ fill: 'var(--text-1)' }}
            >
              {fmtPrice(hoveredCandle.close)}
            </text>
            <rect
              x={clamp(xOfIndex(hover) - 30, padL, padL + plotW - 60)}
              y={height - 18}
              width={60}
              height={15}
              rx={2}
              style={{ fill: 'var(--bg-3)', stroke: 'var(--border-strong)', strokeWidth: 1 }}
            />
            <text
              x={clamp(xOfIndex(hover) - 30, padL, padL + plotW - 60) + 30}
              y={height - 11}
              textAnchor="middle"
              className="chart-axis-text"
              style={{ fill: 'var(--text-1)' }}
            >
              {hoveredCandle.date}
            </text>
          </g>
        )}
      </svg>

      {/* 数据浮层 */}
      {hoveredCandle && hoverInView && hover !== null && (
        <div
          className="chart-tip"
          style={{
            left:
              xOfIndex(hover) > padL + plotW / 2
                ? Math.max(4, xOfIndex(hover) - 210)
                : Math.min(w - 190, xOfIndex(hover) + 16),
            top: 22,
            minWidth: 186,
          }}
        >
          <div className="chart-tip-title">
            <span className="mono">{hoveredCandle.date}</span>
            <span className={colorOf(hoveredCandle) === 'var(--down)' ? 'down' : 'up'}>
              {fmtPct(hoveredCandle.pctChg)}
            </span>
          </div>
          {[
            ['开盘', fmtPrice(hoveredCandle.open)],
            ['最高', fmtPrice(hoveredCandle.high)],
            ['最低', fmtPrice(hoveredCandle.low)],
            ['收盘', fmtPrice(hoveredCandle.close)],
          ].map(([k, v]) => (
            <div className="chart-tip-row" key={k}>
              <span className="chart-tip-k">{k}</span>
              <span className="chart-tip-v">{v}</span>
            </div>
          ))}
          <div className="chart-tip-row">
            <span className="chart-tip-k">成交量</span>
            <span className="chart-tip-v">{fmtVolume(hoveredCandle.volume)}</span>
          </div>
          <div className="chart-tip-row">
            <span className="chart-tip-k">成交额</span>
            <span className="chart-tip-v">{fmtAmount(hoveredCandle.amount)}</span>
          </div>
          <div className="chart-tip-row">
            <span className="chart-tip-k">换手率</span>
            <span className="chart-tip-v">{isNum(hoveredCandle.turnover) ? `${hoveredCandle.turnover.toFixed(2)}%` : '—'}</span>
          </div>
          <div className="chart-tip-row">
            <span className="chart-tip-k">量比</span>
            <span className="chart-tip-v">{fmtTimes(hoveredCandle.volRatio)}</span>
          </div>
          <div className="divider" style={{ margin: '5px 0' }} />
          <div className="chart-tip-row">
            <span className="chart-tip-k" style={{ color: 'var(--gold)' }}>
              MA5
            </span>
            <span className="chart-tip-v">{fmtPrice(ma.ma5[hover])}</span>
          </div>
          <div className="chart-tip-row">
            <span className="chart-tip-k" style={{ color: 'var(--brand)' }}>
              MA10
            </span>
            <span className="chart-tip-v">{fmtPrice(ma.ma10[hover])}</span>
          </div>
          <div className="chart-tip-row">
            <span className="chart-tip-k" style={{ color: 'var(--violet)' }}>
              MA20
            </span>
            <span className="chart-tip-v">{fmtPrice(ma.ma20[hover])}</span>
          </div>
        </div>
      )}

      <div className="row fs-11 text-3" style={{ justifyContent: 'space-between', marginTop: 2 }}>
        <span>滚轮缩放 · 按住拖动平移 · 悬停查看当日明细</span>
        <span className="mono">
          显示 {start + 1} - {end} / {total} 根
        </span>
      </div>
    </div>
  );
}
