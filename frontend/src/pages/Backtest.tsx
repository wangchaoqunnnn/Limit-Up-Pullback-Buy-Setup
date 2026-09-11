import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type { BacktestBySignal, BacktestTrade } from '../api/types';
import { useApi } from '../hooks/useApi';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Stat } from '../components/ui/Stat';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { Table } from '../components/ui/Table';
import type { Column, TableSort } from '../components/ui/Table';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonChart } from '../components/ui/Skeleton';
import { LineChart } from '../components/charts/LineChart';
import { DistributionChart } from '../components/charts/DistributionChart';
import { dataSourceLabel, SIGNAL_META } from '../utils/constants';
import { fmtDate, fmtInt, fmtPct, fmtPrice, fmtScore, isNum } from '../utils/format';

const TRADE_PAGE_SIZE = 20;

/** 回测分析：参数区 + 核心指标 + 净值曲线 + 收益分布 + 分信号统计 + 交易明细 */
export default function Backtest() {
  const [lookbackDays, setLookbackDays] = useState(120);
  const [minScore, setMinScore] = useState(75);
  const [horizon, setHorizon] = useState(5);
  const [tradeSort, setTradeSort] = useState<TableSort>({ field: 'returnPct', order: 'desc' });
  const [tradePage, setTradePage] = useState(1);

  // 刻意不接入全局自动刷新：回测是对历史 K 线的统计结果，参数不变时结果不会变化，
  // 开盘期间每 30 秒重算只会造成无意义的后端负载；需要最新结果时由用户手动点「开始回测」。
  const backtest = useApi((signal) => api.backtest({ lookbackDays, minScore, horizon }, signal), [
    lookbackDays,
    minScore,
    horizon,
  ]);

  const data = backtest.data;

  const sortedTrades = useMemo(() => {
    const trades = [...(data?.trades ?? [])];
    const { field, order } = tradeSort;
    trades.sort((a, b) => {
      const av = a[field as keyof BacktestTrade];
      const bv = b[field as keyof BacktestTrade];
      if (typeof av === 'number' && typeof bv === 'number') return order === 'asc' ? av - bv : bv - av;
      return order === 'asc'
        ? String(av ?? '').localeCompare(String(bv ?? ''))
        : String(bv ?? '').localeCompare(String(av ?? ''));
    });
    return trades;
  }, [data?.trades, tradeSort]);

  const tradePageCount = Math.max(1, Math.ceil(sortedTrades.length / TRADE_PAGE_SIZE));
  const currentTradePage = Math.min(tradePage, tradePageCount);
  const pagedTrades = sortedTrades.slice(
    (currentTradePage - 1) * TRADE_PAGE_SIZE,
    currentTradePage * TRADE_PAGE_SIZE,
  );

  const signalColumns: Column<BacktestBySignal>[] = [
    {
      key: 'name',
      title: '信号',
      render: (row) => (
        <span className="row" style={{ gap: 8 }}>
          <Badge tone={row.winRate >= 0.6 ? 'teal' : row.winRate >= 0.45 ? 'gold' : 'neutral'} dot>
            {SIGNAL_META[row.key]?.short ?? row.name}
          </Badge>
          <span className="fs-12 text-2">{row.name}</span>
        </span>
      ),
    },
    {
      key: 'sampleSize',
      title: '样本数',
      width: 96,
      align: 'right',
      render: (row) => <span className="mono">{fmtInt(row.sampleSize)}</span>,
    },
    {
      key: 'winRate',
      title: '胜率',
      width: 96,
      align: 'right',
      render: (row) => (
        <span className={`mono ${row.winRate >= 0.5 ? 'teal-text' : 'flat'}`}>
          {isNum(row.winRate) ? `${(row.winRate * 100).toFixed(1)}%` : '—'}
        </span>
      ),
    },
    {
      key: 'avgReturn',
      title: '平均收益',
      width: 104,
      align: 'right',
      render: (row) => (
        <span className={`mono ${row.avgReturn >= 0 ? 'up' : 'down'}`}>{fmtPct(row.avgReturn)}</span>
      ),
    },
  ];

  const tradeColumns: Column<BacktestTrade>[] = [
    {
      key: 'code',
      title: '代码 / 名称',
      width: 150,
      render: (row) => (
        <div className="stock-cell">
          <span className="stock-cell-name">{row.name}</span>
          <span className="stock-cell-meta">{row.code}</span>
        </div>
      ),
    },
    { key: 'signalDate', title: '信号日', width: 108, sortable: true, render: (row) => <span className="mono fs-12 text-2">{fmtDate(row.signalDate)}</span> },
    {
      key: 'score',
      title: '评分',
      width: 78,
      align: 'right',
      sortable: true,
      render: (row) => <span className="cell-strong">{fmtScore(row.score)}</span>,
    },
    { key: 'entryPrice', title: '买入价', width: 86, align: 'right', sortable: true, render: (row) => <span className="mono">{fmtPrice(row.entryPrice)}</span> },
    { key: 'exitPrice', title: '卖出价', width: 86, align: 'right', sortable: true, render: (row) => <span className="mono">{fmtPrice(row.exitPrice)}</span> },
    {
      key: 'returnPct',
      title: '区间收益',
      width: 96,
      align: 'right',
      sortable: true,
      render: (row) => (
        <span className={`cell-strong ${row.returnPct >= 0 ? 'up' : 'down'}`}>{fmtPct(row.returnPct)}</span>
      ),
    },
    {
      key: 'maxGainPct',
      title: '最大浮盈',
      width: 96,
      align: 'right',
      sortable: true,
      render: (row) => <span className={`mono ${row.maxGainPct >= 0 ? 'up' : 'flat'}`}>{fmtPct(row.maxGainPct)}</span>,
    },
    {
      key: 'maxLossPct',
      title: '最大浮亏',
      width: 96,
      align: 'right',
      sortable: true,
      render: (row) => <span className="mono down">{fmtPct(row.maxLossPct)}</span>,
    },
    { key: 'holdDays', title: '持有天数', width: 92, align: 'right', sortable: true, render: (row) => <span className="mono">{row.holdDays} 天</span> },
  ];

  return (
    <>
      <PageHeader
        title="回测分析"
        sub={
          data
            ? `回放窗口 ${data.lookbackDays} 日 · 评分门槛 ${data.minScore} · 持有 ${data.horizon} 个交易日 · 数据源 ${dataSourceLabel(data.dataSource)}`
            : '按历史 K 线回放信号日，统计 T+1 起持有期的表现'
        }
      />

      <div className="bt-layout mt-4">
        <div className="col" style={{ gap: 16, minWidth: 0 }}>
          {backtest.error && data && (
            <InlineAlert message={backtest.error} onRetry={backtest.refetch} hint="以下为上一次回测结果。" />
          )}

          {backtest.error && !data && (
            <Card>
              <ErrorState message={backtest.error} onRetry={backtest.refetch} />
            </Card>
          )}

          {/* 核心指标 */}
          <Card title="核心指标" sub="基于全部回测样本统计">
            {backtest.loading && <SkeletonChart height={120} />}
            {!backtest.loading && data && (
              <div className="stat-grid">
                <Stat label="样本数量" value={fmtInt(data.sampleSize)} unit="笔" />
                <Stat
                  label="胜率"
                  value={isNum(data.winRate) ? `${(data.winRate * 100).toFixed(1)}%` : '—'}
                  dir={data.winRate >= 0.5 ? 'up' : 'down'}
                />
                <Stat
                  label="平均收益"
                  value={fmtPct(data.avgReturn)}
                  dir={data.avgReturn >= 0 ? 'up' : 'down'}
                  foot={`持有 ${data.horizon} 日`}
                />
                <Stat label="平均盈利" value={fmtPct(data.avgWin)} dir="up" />
                <Stat label="平均亏损" value={fmtPct(data.avgLoss)} dir="down" />
                <Stat
                  label="盈亏比"
                  value={isNum(data.profitFactor) ? data.profitFactor.toFixed(2) : '—'}
                  foot="总盈利 / 总亏损"
                />
                <Stat
                  label="最大回撤"
                  value={fmtPct(data.maxDrawdown)}
                  dir="down"
                  foot="等权净值曲线"
                />
                <Stat label="平均最大浮盈" value={fmtPct(data.avgMaxGain)} dir="up" />
                <Stat label="平均最大浮亏" value={fmtPct(data.avgMaxLoss)} dir="down" />
              </div>
            )}
            {!backtest.loading && !data && !backtest.error && (
              <EmptyState title="暂无回测结果" desc="当前参数下没有产生可统计的样本。" />
            )}
          </Card>

          {/* 净值曲线 */}
          <Card
            title="净值曲线"
            sub="等权持有期收益累乘指数（起点 100）"
            extra={
              data ? (
                <Badge tone={data.avgReturn >= 0 ? 'up' : 'down'}>
                  {data.returnCurve?.length ? `${fmtPct((data.returnCurve[data.returnCurve.length - 1].index - 100) / 100)}` : '—'}
                </Badge>
              ) : undefined
            }
          >
            {backtest.loading && <SkeletonChart height={270} />}
            {!backtest.loading && data && (data.returnCurve ?? []).length >= 2 && (
              <LineChart
                data={(data.returnCurve ?? []).map((point) => ({ label: point.date, value: point.index }))}
                height={270}
                baseline={100}
                baselineLabel="基准 100"
                seriesName="净值指数"
                valueFormat={(v) => v.toFixed(2)}
                xFormat={(label) => (label.length >= 10 ? label.slice(5) : label)}
                ariaLabel="回测净值曲线"
              />
            )}
            {!backtest.loading && data && (data.returnCurve ?? []).length < 2 && (
              <div className="chart-empty">净值曲线数据不足，无法绘制</div>
            )}
          </Card>

          <div className="grid-2">
            {/* 收益分布 */}
            <Card title="收益分布" sub="按区间收益率分桶统计笔数">
              {backtest.loading ? (
                <SkeletonChart height={230} />
              ) : (
                <DistributionChart data={data?.returnDistribution ?? []} height={230} />
              )}
            </Card>

            {/* 分信号统计 */}
            <Card title="分信号统计" sub="各信号单独命中的样本表现" flush>
              <Table<BacktestBySignal>
                columns={signalColumns}
                rows={(data?.bySignal ?? []).filter(Boolean)}
                rowKey={(row, index) => `${row.key}-${index}`}
                loading={backtest.loading}
                minWidth={520}
                ariaLabel="分信号统计表"
                emptyTitle="暂无分信号统计"
                emptyDesc="回测样本不足，未能按信号拆分统计。"
                skeletonRows={5}
              />
            </Card>
          </div>

          {/* 交易明细 */}
          <Card
            title="交易明细"
            sub={`最多返回 200 条，当前 ${sortedTrades.length} 条`}
            flush
            extra={
              <span className="mono fs-12 text-2">
                第 {currentTradePage} / {tradePageCount} 页
              </span>
            }
          >
            <Table<BacktestTrade>
              columns={tradeColumns}
              rows={pagedTrades}
              rowKey={(row, index) => `${row.code}-${row.signalDate}-${index}`}
              loading={backtest.loading}
              sort={tradeSort}
              onSortChange={(next) => {
                setTradeSort(next);
                setTradePage(1);
              }}
              minWidth={1020}
              ariaLabel="回测交易明细表"
              emptyTitle="暂无交易明细"
              emptyDesc="当前参数下没有命中样本，可降低评分门槛或扩大回放窗口。"
              skeletonRows={8}
              footer={
                <div className="table-foot">
                  <span>
                    共 {fmtInt(sortedTrades.length)} 笔 · 每页 {TRADE_PAGE_SIZE} 笔
                  </span>
                  <div className="pager">
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentTradePage <= 1}
                      onClick={() => setTradePage(1)}
                      aria-label="首页"
                    >
                      «
                    </button>
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentTradePage <= 1}
                      onClick={() => setTradePage(currentTradePage - 1)}
                      aria-label="上一页"
                    >
                      上一页
                    </button>
                    <span className="mono fs-12" style={{ padding: '0 6px' }}>
                      {currentTradePage} / {tradePageCount}
                    </span>
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentTradePage >= tradePageCount}
                      onClick={() => setTradePage(currentTradePage + 1)}
                      aria-label="下一页"
                    >
                      下一页
                    </button>
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentTradePage >= tradePageCount}
                      onClick={() => setTradePage(tradePageCount)}
                      aria-label="末页"
                    >
                      »
                    </button>
                  </div>
                </div>
              }
            />
          </Card>
        </div>

        {/* 参数区 */}
        <Card title="回测参数" sub="调整后自动重新请求" bodyClassName="bt-params">
          <div className="field">
            <label className="field-label" htmlFor="bt-lookback">
              <span>回放窗口</span>
              <span className="slider-value">{lookbackDays} 日</span>
            </label>
            <input
              id="bt-lookback"
              className="range"
              type="range"
              min={30}
              max={500}
              step={10}
              value={lookbackDays}
              onChange={(e) => setLookbackDays(Number(e.target.value))}
            />
            <span className="fs-11 text-3">30 ~ 500 日，窗口越长样本越多</span>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="bt-minscore">
              <span>最低评分门槛</span>
              <span className="slider-value">{minScore}</span>
            </label>
            <input
              id="bt-minscore"
              className="range"
              type="range"
              min={0}
              max={100}
              step={1}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
            />
            <span className="fs-11 text-3">仅统计评分达标的样本</span>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="bt-horizon">
              <span>持有天数</span>
              <span className="slider-value">{horizon} 日</span>
            </label>
            <input
              id="bt-horizon"
              className="range"
              type="range"
              min={1}
              max={10}
              step={1}
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
            />
            <span className="fs-11 text-3">1 ~ 10 个交易日</span>
          </div>

          <div className="divider" />

          <div className="col" style={{ gap: 8 }}>
            <span className="fs-12 text-2">参数说明</span>
            <span className="fs-11 text-3" style={{ lineHeight: 1.8 }}>
              回测按历史 K 线回放到每个「信号日」，以次日开盘价买入、持有期结束收盘价卖出，统计区间收益、最大浮盈与最大浮亏。
              结果不含手续费、滑点与涨跌停无法成交等约束，仅供研究参考。
            </span>
          </div>

          <Button
            block
            variant="primary"
            loading={backtest.refreshing}
            onClick={backtest.refetch}
          >
            按当前参数重新回测
          </Button>
        </Card>
      </div>
    </>
  );
}
