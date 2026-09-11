import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { StockListItem } from '../api/types';
import { InlineAlert } from '../components/ui/ErrorState';
import { useApi, useDebounced } from '../hooks/useApi';
import { useAutoRefreshTarget } from '../hooks/useAutoRefresh';
import { useWatchlist } from '../hooks/useWatchlist';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Table } from '../components/ui/Table';
import type { Column } from '../components/ui/Table';
import { SEARCH_DEBOUNCE_MS } from '../utils/constants';
import { Sparkline } from '../components/charts/Sparkline';
import { VerdictBadge } from '../components/stock/VerdictBadge';
import { LimitUpTypeTag } from '../components/stock/LimitUpTypeTag';
import { fmtAmount, fmtCount, fmtDate, fmtInt, fmtPct, fmtPrice, fmtScore, isNum } from '../utils/format';

const PAGE_SIZES = [20, 50, 100];

/** 股票池浏览：关键字防抖搜索 + 行业过滤 + 分页表格 + 迷你走势图 */
export default function Stocks() {
  const [keywordInput, setKeywordInput] = useState('');
  const keyword = useDebounced(keywordInput, SEARCH_DEBOUNCE_MS);
  const [industry, setIndustry] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const watchlist = useWatchlist();

  const queryKey = JSON.stringify({ keyword, industry, page, pageSize });
  const list = useApi((signal) => api.stocks({ keyword: keyword || undefined, industry: industry || undefined, page, pageSize }, signal), [
    queryKey,
  ]);
  const industrySource = useApi((signal) => api.stocks({ page: 1, pageSize: 200 }, signal), []);

  // 开盘期间自动刷新列表主数据（静默刷新：保留旧数据与分页位置）
  useAutoRefreshTarget(list.refetchAsync);

  const industries = useMemo(() => {
    const set = new Set<string>();
    (industrySource.data?.items ?? []).forEach((item) => {
      if (item.meta?.industry) set.add(item.meta.industry);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
  }, [industrySource.data]);

  const total = list.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, pageCount);

  const columns: Column<StockListItem>[] = [
    {
      key: 'meta.code',
      title: '代码 / 名称',
      width: 168,
      render: (item) => (
        <div className="stock-cell">
          <span className="stock-cell-name">
            <Link to={`/stock/${item.meta?.code}`}>{item.meta?.name ?? '—'}</Link>
            {item.meta?.isSt && <span className="tag tag-danger">ST</span>}
            {watchlist.has(item.meta?.code ?? '') && <span className="gold-text" title="已关注">★</span>}
          </span>
          <span className="stock-cell-meta">
            {item.meta?.code} · {item.meta?.board}
          </span>
        </div>
      ),
    },
    {
      key: 'meta.industry',
      title: '行业',
      width: 100,
      render: (item) => <span className="fs-12 text-2">{item.meta?.industry ?? '—'}</span>,
    },
    {
      key: 'lastClose',
      title: '现价',
      width: 84,
      align: 'right',
      render: (item) => <span className="cell-strong">{fmtPrice(item.lastClose)}</span>,
    },
    {
      key: 'pctChg',
      title: '涨跌幅',
      width: 92,
      align: 'right',
      render: (item) => (
        <span className={`mono ${isNum(item.pctChg) && item.pctChg > 0 ? 'up' : isNum(item.pctChg) && item.pctChg < 0 ? 'down' : 'flat'}`}>
          {fmtPct(item.pctChg)}
        </span>
      ),
    },
    {
      key: 'turnover',
      title: '换手率',
      width: 88,
      align: 'right',
      render: (item) => (
        <span className="mono">{isNum(item.turnover) ? `${item.turnover.toFixed(2)}%` : '—'}</span>
      ),
    },
    {
      key: 'amount',
      title: '成交额',
      width: 96,
      align: 'right',
      render: (item) => <span className="mono">{fmtAmount(item.amount)}</span>,
    },
    {
      key: 'limitUpType',
      title: '涨停类型',
      width: 96,
      render: (item) => <LimitUpTypeTag type={item.limitUpType} />,
    },
    {
      key: 'sparkline',
      title: '近期走势',
      width: 104,
      render: (item) => <Sparkline data={item.sparkline ?? []} width={76} height={26} />,
    },
    {
      key: 'score',
      title: '评分',
      width: 78,
      align: 'right',
      render: (item) => <span className="cell-strong">{isNum(item.score) ? fmtScore(item.score) : '—'}</span>,
    },
    {
      key: 'verdict',
      title: '结论',
      width: 86,
      render: (item) => <VerdictBadge verdict={item.verdict} size="sm" />,
    },
    {
      key: 'actions',
      title: '操作',
      width: 128,
      render: (item) => {
        const code = item.meta?.code ?? '';
        return (
          <span className="row" style={{ gap: 6 }}>
            <Button
              size="sm"
              variant={watchlist.has(code) ? 'gold' : 'default'}
              onClick={() => watchlist.toggle(code)}
              aria-label={watchlist.has(code) ? `取消关注 ${item.meta?.name}` : `关注 ${item.meta?.name}`}
            >
              {watchlist.has(code) ? '已关注' : '关注'}
            </Button>
            <Link className="btn btn-sm btn-ghost" to={`/stock/${code}`}>
              详情
            </Link>
          </span>
        );
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="股票池"
        sub={
          list.data
            ? `共 ${fmtInt(list.data.total)} 只标的 · 第 ${currentPage} / ${pageCount} 页 · 数据更新至 ${fmtDate(list.data.items?.[0]?.lastDate)}`
            : '全市场行情浏览，支持代码 / 名称模糊搜索'
        }
        actions={
          <Button loading={list.refreshing} onClick={list.refetch}>
            刷新列表
          </Button>
        }
      />

      {list.error && list.data && (
        <div className="mt-2">
          <InlineAlert message={list.error} onRetry={list.refetch} hint="以下为上一次成功获取的列表。" />
        </div>
      )}

      <Card className="mt-4">
        <div className="row wrap" style={{ gap: 12, alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: '1 1 240px' }}>
            <label className="field-label" htmlFor="stock-keyword">
              <span>关键字搜索</span>
              <span className="fs-11 text-3">代码或名称，300ms 防抖</span>
            </label>
            <input
              id="stock-keyword"
              className="input"
              type="search"
              placeholder="例如 600519 或 贵州茅台"
              value={keywordInput}
              onChange={(e) => {
                setKeywordInput(e.target.value);
                setPage(1);
              }}
            />
          </div>

          <div className="field" style={{ flex: '0 1 200px' }}>
            <label className="field-label" htmlFor="stock-industry">
              <span>行业过滤</span>
            </label>
            <select
              id="stock-industry"
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
          </div>

          <div className="field" style={{ flex: '0 0 140px' }}>
            <label className="field-label" htmlFor="stock-pagesize">
              <span>每页条数</span>
            </label>
            <select
              id="stock-pagesize"
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

          <Button
            onClick={() => {
              setKeywordInput('');
              setIndustry('');
              setPage(1);
            }}
          >
            清空条件
          </Button>
        </div>
      </Card>

      <Card className="mt-4" flush>
        <Table<StockListItem>
          columns={columns}
          rows={list.data?.items ?? []}
          rowKey={(item, index) => item.meta?.code ?? `row-${index}`}
          loading={list.loading}
          error={list.error && !list.data ? list.error : null}
          onRetry={list.refetch}
          minWidth={1080}
          ariaLabel="股票池列表"
          emptyTitle="未找到匹配的股票"
          emptyDesc="请更换关键字或行业条件后重试。"
          footer={
            <div className="table-foot">
              <span>
                共 {fmtCount(total)} 只 · 当前显示 {(list.data?.items ?? []).length} 只
                {keyword ? ` · 关键字「${keyword}」` : ''}
                {industry ? ` · 行业「${industry}」` : ''}
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
    </>
  );
}
