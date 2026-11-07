import { useState } from 'react'
import { api } from '../../api/client'
import type { EvalResultItem, EvalRunResponse } from '../../types'
import { fmtValue, pick, pickList, usePageData, LoadingBlock, ErrBlock, EmptyBlock, ACard, PageHead, Tag, Btn } from './ui'
import { BarChart } from '../charts/BarChart'

/* ============================================================
 * 后台 ④ 评测(§11 GET /api/admin/eval + POST /api/admin/eval/run)
 * golden 列表 + 运行评测 + 结果表 + 历史
 * ============================================================ */

const KIND_TONES: Record<string, 'blue' | 'purple' | 'teal' | 'orange' | 'green' | 'red' | 'gray'> = {
  value: 'blue',
  trend: 'teal',
  rank: 'purple',
  compare: 'green',
  attribution: 'orange',
  clarify: 'gray',
  refuse: 'red',
  boundary: 'gray',
}

function normalize(data: unknown) {
  const golden = pickList(data, ['golden', 'golden_qa', 'goldens', 'questions'])
  const history = pickList(data, ['eval_history', 'history'])
  return { golden, history }
}

export function EvalPage() {
  const { data, status, err, reload } = usePageData<unknown>('/api/admin/eval')
  const { golden, history } = normalize(data)
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<EvalRunResponse | null>(null)
  const [runErr, setRunErr] = useState('')

  const last = history.length ? history[0] : null

  const run = async () => {
    setRunning(true)
    setRunErr('')
    try {
      const rep = await api<EvalRunResponse>('/api/admin/eval/run', { method: 'POST' })
      setReport(rep && typeof rep === 'object' ? rep : {})
      reload()
    } catch (e) {
      setRunErr(e instanceof Error ? e.message : '评测失败')
    } finally {
      setRunning(false)
    }
  }

  const reportData: EvalRunResponse | null = report
  const results: EvalResultItem[] = reportData
    ? pickList(reportData, ['results', 'report', 'details'])
    : []
  const passRate =
    reportData?.pass_rate ??
    (typeof reportData?.total === 'number' && reportData.total > 0 && typeof reportData?.passed === 'number'
      ? Math.round((reportData.passed / reportData.total) * 10000) / 100
      : undefined)

  /* 历史通过率趋势 */
  const histSpec =
    history.length > 0
      ? {
          kind: 'bar' as const,
          x: history
            .slice()
            .reverse()
            .map((h) => String(pick(h, ['ts', 'time']) ?? '')),
          data: history
            .slice()
            .reverse()
            .map((h) => Number(pick(h, ['pass_rate']) ?? 0)),
          title: '',
        }
      : null

  return (
    <div className="biz-admin">
      <div className="biz-admin-inner">
        <PageHead
          title="评测"
          desc="用固定题库检验问答链路的准确率,可反复运行"
          right={<Btn variant="primary" icon="flask" loading={running} onClick={() => void run()}>运行评测</Btn>}
        />

        {status === 'loading' ? (
          <LoadingBlock />
        ) : status === 'error' ? (
          <ErrBlock msg={err} onRetry={reload} />
        ) : (
          <>
            {runErr ? (
              <div style={{ marginBottom: 14 }}>
                <ErrBlock msg={runErr} onRetry={() => void run()} />
              </div>
            ) : null}

            {reportData ? (
              <ACard title="最近一次评测报告" icon="check-circle">
                <div className="biz-gates" style={{ marginBottom: results.length ? 14 : 0 }}>
                  <div className="biz-gate">
                    <div className="biz-gate-val biz-num">
                      {fmtValue(reportData.passed ?? results.filter((r) => r.passed).length)} / {fmtValue(reportData.total ?? results.length)}
                    </div>
                    <div className="biz-gate-label">通过 / 总数</div>
                  </div>
                  <div className="biz-gate">
                    <div className="biz-gate-val biz-num">{passRate !== undefined ? `${passRate}%` : '—'}</div>
                    <div className="biz-gate-label">通过率</div>
                  </div>
                  <div className="biz-gate">
                    <div className="biz-gate-val">{reportData.mode === 'llm' ? 'LLM' : '规则兜底'}</div>
                    <div className="biz-gate-label">评测模式</div>
                  </div>
                </div>
                {results.length ? (
                  <div className="biz-table-wrap">
                    <table className="biz-table">
                      <thead>
                        <tr>
                          <th>问题</th>
                          <th>类型</th>
                          <th>结果</th>
                          <th>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.map((r, i) => (
                          <tr key={i}>
                            <td>{r.question ?? '—'}</td>
                            <td>
                              <Tag tone={KIND_TONES[String(r.kind ?? '')] ?? 'gray'}>{r.kind ?? '—'}</Tag>
                            </td>
                            <td>
                              <Tag tone={r.passed ? 'green' : 'red'}>{r.passed ? '通过' : '未通过'}</Tag>
                            </td>
                            <td style={{ color: 'var(--c-text2)', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {r.actual ? `实际 ${r.actual}` : (r.note ?? '—')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </ACard>
            ) : null}

            <ACard title={`评测题库(${golden.length})`} icon="book" right={last ? `上次:通过率 ${fmtValue(pick(last, ['pass_rate']))}%` : undefined}>
              {golden.length ? (
                <div className="biz-table-wrap">
                  <table className="biz-table">
                    <thead>
                      <tr>
                        <th style={{ width: 44 }}>#</th>
                        <th>问题</th>
                        <th>类型</th>
                        <th>期望指标</th>
                        <th>标签</th>
                      </tr>
                    </thead>
                    <tbody>
                      {golden.map((g, i) => (
                        <tr key={i}>
                          <td>{fmtValue(pick(g, ['id']))}</td>
                          <td>{String(pick(g, ['question']) ?? '—')}</td>
                          <td>
                            <Tag tone={KIND_TONES[String(pick(g, ['kind']))] ?? 'gray'}>{fmtValue(pick(g, ['kind']))}</Tag>
                          </td>
                          <td style={{ color: 'var(--c-text3)' }}>{fmtValue(pick(g, ['expect_metric']))}</td>
                          <td>
                            <Tag tone="gray">{fmtValue(pick(g, ['tag']))}</Tag>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyBlock text="题库为空" />
              )}
            </ACard>

            <ACard title="评测历史" icon="clock">
              {history.length && histSpec ? (
                <>
                  <BarChart spec={histSpec} height={180} />
                  <div className="biz-table-wrap" style={{ marginTop: 12 }}>
                    <table className="biz-table">
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>模式</th>
                          <th className="r">通过 / 总数</th>
                          <th className="r">通过率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history.map((h, i) => (
                          <tr key={i}>
                            <td>{fmtValue(pick(h, ['ts', 'time']))}</td>
                            <td>{Number(pick(h, ['mode'])) === 1 || pick(h, ['mode']) === 'llm' ? 'LLM' : '规则'}</td>
                            <td className="r">
                              {fmtValue(pick(h, ['passed']))} / {fmtValue(pick(h, ['total']))}
                            </td>
                            <td className="r">{fmtValue(pick(h, ['pass_rate']))}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <EmptyBlock text="还没有评测记录,点右上角「运行评测」" />
              )}
            </ACard>
          </>
        )}
      </div>
    </div>
  )
}

