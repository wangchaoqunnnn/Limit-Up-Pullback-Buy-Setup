import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, isApiError, toErrorMessage } from '../api/client';
import type { SortField, SortOrder, StockSignal, Verdict } from '../api/types';
import { useApi } from '../hooks/useApi';
import { useAutoRefreshTarget } from '../hooks/useAutoRefresh';
import { useScanTask } from '../hooks/useScanTask';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Stat } from '../components/ui/Stat';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { ProgressBar, ProgressLine } from '../components/ui/ProgressBar';
import { Segmented } from '../components/ui/Segmented';
import { useToast } from '../components/ui/Toast';
import { SkeletonRows } from '../components/ui/Skeleton';
import { StockTable } from '../components/stock/StockTable';
import type { TableSort } from '../components/ui/Table';
import {
  LIMIT_UP_TYPE_OPTIONS,
  SIGNAL_KEYS,
  SIGNAL_META,
  SORT_OPTIONS,
  VERDICT_OPTIONS,
  dataSourceLabel,
} from '../utils/constants';
import { fmtDateTime, fmtInt, fmtScore, isNum } from '../utils/format';

const PAGE_SIZES = [20, 50, 100, 200];

/** 信号选股（核心页）：左侧筛选面板 + 右侧结果表格 + 统计条 + 展开信号明细 */
export default function Signals() {
  const navigate = useNavigate();
  const toast = useToast();

  const [verdicts, setVerdicts] = useState<Verdict[]>(['BUY', 'WATCH']);
  const [industry, setIndustry] = useState('');
  const [minScore, setMinScore] = useState(0);
  const [maxScore, setMaxScore] = useState(100);
  const [limitUpType, setLimitUpType] = useState('QUALITY');
  const [sort, setSort] = useState<SortField>('score');
  const [order, setOrder] = useState<SortOrder>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [addingCode, setAddingCode] = useState<string | null>(null);

  const query = useMemo(
    () => ({
      verdict: verdicts.length > 0 ? verdicts.join(',') : 'BUY,WATCH,REJECT',
      industry: industry || undefined,
      minScore,
      maxScore,
      limitUpType,
      sort,
      order,
      page,
      pageSize,
    }),
    [verdicts, industry, minScore, maxScore, limitUpType, sort, order, page, pageSize],
  );
  const queryKey = JSON.stringify(query);

  const signals = useApi((signal) => api.signals(query, signal), [queryKey]);
  const industrySource = useApi((signal) => api.stocks({ page: 1, pageSize: 200 }, signal), []);

  const scan = useScanTask(() => {
    signals.refetch();
    toast.success('全市场扫描完成，选股结果已刷新');
  });

  // 开盘期间自动刷新当前筛选条件下的选股结果（静默刷新，参数不变时不重算扫描）
  useAutoRefreshTarget(signals.refetchAsync);

  const industries = useMemo(() => {
    const set = new Set<string>();
    (industrySource.data?.items ?? []).forEach((item) => {
      if (item.meta?.industry) set.add(item.meta.industry);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
  }, [industrySource.data]);

  const statistics = signals.data?.statistics;
  const total = signals.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, pageCount);

  const toggleVerdict = (value: Verdict) => {
    setPage(1);
    setVerdicts((prev) => (prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]));
  };

  const resetFilters = () => {
    setVerdicts(['BUY', 'WATCH']);
    setIndustry('');
    setMinScore(0);
    setMaxScore(100);
    setLimitUpType('QUALITY');
    setSort('score');
    setOrder('desc');
    setPage(1);
    setPageSize(50);
  };

  const handleSortChange = (next: TableSort) => {
    setSort(next.field as SortField);
    setOrder(next.order);
    setPage(1);
  };

  const addToPool = async (item: StockSignal) => {
    const code = item.meta?.code;
    if (!code) return;
    setAddingCode(code);
    try {
      await api.addPool({
        code,
        buyLow: isNum(item.plan?.buyLow) ? item.plan.buyLow : undefined,
        buyHigh: isNum(item.plan?.buyHigh) ? item.plan.buyHigh : undefined,
        stopLoss: isNum(item.plan?.stopLoss) ? item.plan.stopLoss : undefined,
        takeProfit1: isNum(item.plan?.takeProfit1) ? item.plan.takeProfit1 : undefined,
        takeProfit2: isNum(item.plan?.takeProfit2) ? item.plan.takeProfit2 : undefined,
        note: item.signalSummary || undefined,
      });
      toast.success(`${item.meta?.name ?? code} 已加入低吸池`);
    } catch (err) {
      if (isApiError(err) && err.status === 409) {
        toast.warn(`${item.meta?.name ?? code} 已在低吸池中，无需重复添加`);
      } else {
        toast.error(`加入低吸池失败：${toErrorMessage(err)}`);
      }
    } finally {
      setAddingCode(null);
    }
  };

  return (
    <>
      <PageHeader
        title="信号选股"
        sub={
          signals.data
            ? `${signals.data.tradeDate} · 扫描时间 ${fmtDateTime(signals.data.scannedAt)} · 全市场 ${fmtInt(signals.data.universeSize)} 只 · 数据源 ${dataSourceLabel(signals.data.dataSource)}`
            : '按五大共振信号筛选当前可低吸标的'
        }
        actions={
          <Button
            variant="primary"
            loading={scan.running}
            title="重新回放全市场历史 K 线并刷新选股结果"
            onClick={() => {
              void scan.start({ refresh: true, limitUpType });
            }}
          >
            重新扫描全市场
          </Button>
        }
      />

      {signals.error && signals.data && (
        <div className="mt-2">
          <InlineAlert message={signals.error} onRetry={signals.refetch} hint="以下为上一次扫描结果。" />
        </div>
      )}

      {(scan.task || scan.error) && (
        <Card
          title="扫描任务"
          sub={scan.task?.message ?? '正在触发全市场扫描…'}
          extra={
            <Badge
              tone={scan.task?.status === 'finished' ? 'teal' : scan.task?.status === 'failed' ? 'danger' : 'brand'}
              dot
            >
              {scan.task?.status === 'finished' ? '已完成' : scan.task?.status === 'failed' ? '失败' : '扫描中'}
            </Badge>
          }
          className="mt-2"
        >
          {scan.error ? (
            <ErrorState message={scan.error} compact onRetry={() => void scan.start({ refresh: true })} />
          ) : (
            <ProgressBar
              value={scan.progressPct / 100}
              size="lg"
              showValue
              label={`进度 ${scan.task?.progress ?? 0} / ${scan.task?.total ?? 0}`}
              ariaLabel="扫描进度"
            />
          )}
        </Card>
      )}

      <div className="signals-layout mt-4">
        {/* 筛选面板 */}
        <Card title="筛选条件" sub="调整后自动重新请求" bodyClassName="filter-panel">
          <div className="filter-group">
            <div className="filter-title">
              <span>结论</span>
              <span className="fs-11 text-3">可多选</span>
            </div>
            {VERDICT_OPTIONS.map((option) => (
              <label className="checkbox" key={option.value}>
                <input
                  type="checkbox"
                  checked={verdicts.includes(option.value)}
                  onChange={() => toggleVerdict(option.value)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>

          <div className="filter-group">
            <label className="filter-title" htmlFor="filter-industry">
              <span>所属行业</span>
            </label>
            <select
              id="filter-industry"
              className="select"
              value={industry}
              onChange={(e) => {
                setIndustry(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部行业</option>
              {industries.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {industrySource.error && !industrySource.data && (
              <span className="fs-11 text-3">行业列表加载失败，可直接使用默认全部行业</span>
            )}
          </div>

          <div className="filter-group">
            <div className="filter-title">
              <span>评分区间</span>
              <span className="slider-value">
                {minScore} ~ {maxScore}
              </span>
            </div>
            {/* 单轨道双柄区间滑块：两个手柄共享同一条轨道，中间为已选区间 */}
            <div className="range-dual">
              <span className="range-dual-track" aria-hidden="true" />
              <span
                className="range-dual-fill"
                aria-hidden="true"
                style={{ left: `${minScore}%`, right: `${100 - maxScore}%` }}
              />
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={minScore}
                aria-label="最低评分"
                title={`最低评分 ${minScore}`}
                onChange={(e) => {
                  const value = Math.min(Number(e.target.value), maxScore);
                  setMinScore(value);
                  setPage(1);
                }}
              />
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={maxScore}
                aria-label="最高评分"
                title={`最高评分 ${maxScore}`}
                onChange={(e) => {
                  const value = Math.max(Number(e.target.value), minScore);
                  setMaxScore(value);
                  setPage(1);
                }}
              />
            </div>
            <div className="range-bounds">
              <span>0</span>
              <span>100</span>
            </div>
          </div>

          <div className="filter-group">
            <label className="filter-title" htmlFor="filter-type">
              <span>涨停类型</span>
            </label>
            <select
              id="filter-type"
              className="select"
              value={limitUpType}
              onChange={(e) => {
                setLimitUpType(e.target.value);
                setPage(1);
              }}
            >
              {LIMIT_UP_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="filter-group">
            <label className="filter-title" htmlFor="filter-sort">
              <span>排序字段</span>
            </label>
            <select
              id="filter-sort"
              className="select"
              value={sort}
              onChange={(e) => {
                setSort(e.target.value as SortField);
                setPage(1);
              }}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="seg-label" id="sort-order-label">
              排序方向
            </span>
            <Segmented<SortOrder>
              value={order}
              ariaLabel="排序方向"
              className="seg-compact"
              options={[
                { value: 'desc', label: '降序' },
                { value: 'asc', label: '升序' },
              ]}
              onChange={(value) => {
                setOrder(value);
                setPage(1);
              }}
            />
          </div>

          <div className="filter-group">
            <label className="filter-title" htmlFor="filter-pagesize">
              <span>每页条数</span>
            </label>
            <select
              id="filter-pagesize"
              className="select"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size} 条 / 页
                </option>
              ))}
            </select>
          </div>

          <div className="filter-actions">
            <Button
              variant="primary"
              loading={signals.refreshing}
              onClick={() => {
                setPage(1);
                signals.refetch();
              }}
            >
              应用筛选
            </Button>
            <Button onClick={resetFilters}>重置</Button>
          </div>
        </Card>

        {/* 结果区 */}
        <div className="col" style={{ gap: 16 }}>
          <Card title="统计概览" sub="基于当前筛选结果集计算">
            {signals.loading && <SkeletonRows rows={1} cols={5} />}
            {!signals.loading && signals.error && !signals.data && (
              <ErrorState message={signals.error} compact onRetry={signals.refetch} />
            )}
            {!signals.loading && statistics && (
              <div className="col" style={{ gap: 14 }}>
                <div className="stats-strip">
                  <Stat size="sm" label="命中标的" value={fmtInt(signals.data?.matched ?? total)} unit="只" />
                  <Stat size="sm" label="可低吸" value={fmtInt(statistics.buyCount)} unit="只" dir="brand" />
                  <Stat size="sm" label="观察" value={fmtInt(statistics.watchCount)} unit="只" />
                  <Stat size="sm" label="平均评分" value={fmtScore(statistics.avgScore)} unit="分" />
                  <Stat
                    size="sm"
                    label="平均回调天数"
                    value={isNum(statistics.avgPullbackDays) ? statistics.avgPullbackDays.toFixed(1) : '—'}
                    unit="天"
                  />
                </div>
                <div className="grid-2">
                  {SIGNAL_KEYS.map((key) => {
                    const rate = statistics.signalPassRate?.[key];
                    const value = isNum(rate) ? rate : 0;
                    return (
                      <ProgressLine
                        key={key}
                        value={value}
                        tone={value >= 0.6 ? 'teal' : value >= 0.35 ? 'gold' : 'flat'}
                        label={SIGNAL_META[key].short}
                        percentText={`${(value * 100).toFixed(1)}%`}
                      />
                    );
                  })}
                </div>
              </div>
            )}
            {!signals.loading && !statistics && !signals.error && (
              <span className="fs-12 text-3">当前条件下暂无统计数据。</span>
            )}
          </Card>

          <Card
            title="选股结果"
            sub="点击表头排序，点击行展开五大信号明细"
            extra={
              <span className="fs-12 text-2">
                命中 <span className="mono">{fmtInt(total)}</span> 条
              </span>
            }
            flush
          >
            <StockTable
              items={signals.data?.items ?? []}
              loading={signals.loading}
              error={signals.error && !signals.data ? signals.error : null}
              onRetry={signals.refetch}
              sort={{ field: sort, order }}
              onSortChange={handleSortChange}
              onRowClick={(item) =>
                setExpanded((prev) => (prev === item.meta?.code ? null : (item.meta?.code ?? null)))
              }
              expandedKey={expanded}
              onAddPool={addToPool}
              addingCode={addingCode}
              footer={
                <div className="table-foot">
                  <span>
                    共 {fmtInt(total)} 条 · 当前第 {currentPage} 页 · 每页 {pageSize} 条
                    {signals.data ? ` · 数据源 ${dataSourceLabel(signals.data.dataSource)}` : ''}
                  </span>
                  <div className="pager">
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentPage <= 1}
                      onClick={() => setPage(1)}
                      aria-label="首页"
                    >
                      «
                    </button>
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentPage <= 1}
                      onClick={() => setPage(currentPage - 1)}
                      aria-label="上一页"
                    >
                      上一页
                    </button>
                    {Array.from({ length: Math.min(5, pageCount) }, (_, i) => {
                      const base = Math.max(1, Math.min(currentPage - 2, pageCount - 4));
                      const pageNo = base + i;
                      if (pageNo > pageCount) return null;
                      return (
                        <button
                          key={pageNo}
                          type="button"
                          className={`pager-btn${pageNo === currentPage ? ' active' : ''}`}
                          onClick={() => setPage(pageNo)}
                          aria-current={pageNo === currentPage ? 'page' : undefined}
                        >
                          {pageNo}
                        </button>
                      );
                    })}
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentPage >= pageCount}
                      onClick={() => setPage(currentPage + 1)}
                      aria-label="下一页"
                    >
                      下一页
                    </button>
                    <button
                      type="button"
                      className="pager-btn"
                      disabled={currentPage >= pageCount}
                      onClick={() => setPage(pageCount)}
                      aria-label="末页"
                    >
                      »
                    </button>
                  </div>
                </div>
              }
            />
          </Card>

          <div className="row" style={{ gap: 8 }}>
            <Button size="sm" onClick={() => navigate('/rules')}>
              查看战法规则与权重
            </Button>
            <Button size="sm" onClick={() => navigate('/backtest')}>
              查看历史回测表现
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
