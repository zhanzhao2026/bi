import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { SettingsResponse, SettingsTestResponse } from '../../types'
import { fmtValue, usePageData, LoadingBlock, ErrBlock, ACard, PageHead, Tag, Btn } from './ui'
import { useToast } from '../Toast'
import { Icon } from '../Icon'

/* ============================================================
 * 后台 ⑤ 系统设置(§11 GET/PUT settings + POST settings/test)
 * LLM 表单 + 测试连接 + 保存;顶部模式徽章
 * ============================================================ */

export function SettingsPage() {
  const toast = useToast()
  const { data, status, err, reload } = usePageData<SettingsResponse>('/api/admin/settings')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [token, setToken] = useState('')
  const [tokenDirty, setTokenDirty] = useState(false)
  const [formTouched, setFormTouched] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<SettingsTestResponse | null>(null)

  useEffect(() => {
    if (!data) return
    setBaseUrl(String(data.base_url ?? ''))
    setModel(String(data.model ?? ''))
    setToken('')
    setTokenDirty(false)
    setFormTouched(false)
  }, [data])

  const savePayload = (includeKey: boolean): Record<string, unknown> => {
    const body: Record<string, unknown> = {
      llm_base_url: baseUrl,
      llm_model: model,
      base_url: baseUrl,
      model,
    }
    /* 仅在用户主动填写/清除 token 时才提交 api_key(空串=清除) */
    if (includeKey && (tokenDirty || token !== '')) {
      body.llm_api_key = token
      body.api_key = token
    }
    return body
  }

  const doSave = async (silent: boolean): Promise<boolean> => {
    setSaving(true)
    try {
      await api('/api/admin/settings', { method: 'PUT', body: savePayload(true) })
      await reload()
      if (!silent) toast('设置已保存', 'success')
      return true
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  const doTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      /* 测试前先保存表单,保证测的是当前填写内容 */
      if (formTouched || tokenDirty) {
        const okSave = await doSave(true)
        if (!okSave) {
          setTesting(false)
          return
        }
      }
      const r = await api<SettingsTestResponse>('/api/admin/settings/test', { method: 'POST' })
      setTestResult(r && typeof r === 'object' ? r : {})
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof Error ? e.message : '测试失败' })
    } finally {
      setTesting(false)
    }
  }

  const llmEnabled = data?.llm_enabled === true

  return (
    <div className="biz-admin">
      <div className="biz-admin-inner">
        <PageHead
          title="系统设置"
          desc="配置大模型网关;不填 Token 时全链路自动走规则兜底,演示不中断"
          right={
            llmEnabled ? <Tag tone="green">LLM 已接入</Tag> : <Tag tone="orange">规则兜底模式</Tag>
          }
        />
        {status === 'loading' ? (
          <LoadingBlock />
        ) : status === 'error' ? (
          <ErrBlock msg={err} onRetry={reload} />
        ) : (
          <ACard title="大模型接入" icon="settings">
            <div className="biz-form">
              <div className="biz-field">
                <label>服务地址(base_url)</label>
                <input
                  value={baseUrl}
                  onChange={(e) => {
                    setBaseUrl(e.target.value)
                    setFormTouched(true)
                  }}
                  placeholder="https://open.bigmodel.cn/api/paas/v4"
                />
              </div>
              <div className="biz-field">
                <label>模型(model)</label>
                <input
                  value={model}
                  onChange={(e) => {
                    setModel(e.target.value)
                    setFormTouched(true)
                  }}
                  placeholder="glm-4-flash"
                />
              </div>
              <div className="biz-field">
                <label>API Token</label>
                <input
                  type="password"
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value)
                    setTokenDirty(true)
                  }}
                  placeholder={data?.has_key ? `已配置(${data.key_masked ?? 'sk-***'})` : '未配置,留空则使用规则兜底'}
                  autoComplete="new-password"
                />
                <div className="biz-field-note">
                  Token 只保存在服务端,页面不回显;清空并保存即清除。
                </div>
              </div>
              <div className="biz-form-actions">
                <Btn variant="primary" icon="check" loading={saving} onClick={() => void doSave(false)}>
                  保存
                </Btn>
                <Btn icon="flask" loading={testing} onClick={() => void doTest()}>
                  测试连接
                </Btn>
                <span style={{ fontSize: 12, color: 'var(--c-text3)' }}>
                  当前状态:{llmEnabled ? 'LLM 已接入' : '规则兜底模式'}
                </span>
              </div>
              {testResult ? (
                <div className={`biz-test-result ${testResult.ok ? 'ok' : 'fail'}`}>
                  <Icon name={testResult.ok ? 'check-circle' : 'alert-triangle'} />
                  {testResult.ok ? (
                    <span>
                      连接成功 · {testResult.model ?? model} · {fmtValue(testResult.latency_ms)} ms
                      {testResult.reply ? ` · 回复:「${String(testResult.reply).slice(0, 40)}」` : ''}
                    </span>
                  ) : (
                    <span>连接失败:{testResult.error ?? '未知原因'}</span>
                  )}
                </div>
              ) : null}
            </div>
          </ACard>
        )}
      </div>
    </div>
  )
}
