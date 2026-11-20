// 脚本编辑器：三栏（节点列表 / 节点表单+脚本设置 / JSON 源码）+ 保存/校验/运行
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  App,
  Button,
  Collapse,
  Empty,
  Result,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  PlayCircleOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'

import { describeApiError, validationErrorsOf } from '@/api/client'
import { useScript, useUpdateScript, useValidateScriptData } from '@/api/scripts'
import type { NodeType, ScriptData, ScriptValidation, StepNode } from '@/types/api'
import { prettyJson, safeParseJson } from '@/lib/format'

import JsonSourcePanel from './JsonSourcePanel'
import NodeForm from './NodeForm'
import NodeList from './NodeList'
import RunScriptModal from './RunScriptModal'
import ScriptSettingsForm from './ScriptSettingsForm'
import { createStep, normalizeDraft, normalizeScriptData } from './nodeDefaults'

function parseJsonToData(text: string): { data?: ScriptData; error?: string } {
  const parsed = safeParseJson<unknown>(text)
  if (!parsed.ok) return { error: parsed.error }
  const value = parsed.value
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { error: '顶层必须是 JSON 对象' }
  const record = value as Partial<ScriptData>
  if (!Array.isArray(record.steps)) return { error: '缺少 steps 数组' }
  if (typeof record.name !== 'string' || !record.name.trim()) return { error: '缺少 name 字段（脚本名称）' }
  return { data: record as ScriptData }
}

function stepIndexFromPath(path: string): number | null {
  const match = path.match(/steps[.[](\d+)/)
  if (!match) return null
  const index = Number(match[1])
  return Number.isFinite(index) ? index : null
}

export default function ScriptEditorPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { message } = App.useApp()

  const scriptQuery = useScript(id)
  const updateMutation = useUpdateScript(id)
  const validateMutation = useValidateScriptData()

  const [draft, setDraft] = useState<ScriptData | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [jsonDirty, setJsonDirty] = useState(false)
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [validation, setValidation] = useState<ScriptValidation | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [runOpen, setRunOpen] = useState(false)

  const loadedIdRef = useRef<string | null>(null)

  const script = scriptQuery.data

  useEffect(() => {
    if (!script) return
    if (loadedIdRef.current === script.id) return
    loadedIdRef.current = script.id
    const data = normalizeScriptData(script)
    setDraft(data)
    setSelectedId(data.steps[0]?.id ?? null)
    setJsonText(prettyJson(data))
    setJsonDirty(false)
    setJsonError(null)
    setDirty(false)
    setValidation(null)
  }, [script])

  const selectedIndex = useMemo(() => {
    if (!draft || !selectedId) return -1
    return draft.steps.findIndex((step) => step.id === selectedId)
  }, [draft, selectedId])

  const selectedNode = draft && selectedIndex >= 0 ? draft.steps[selectedIndex] : null

  const applyDraft = (next: ScriptData): void => {
    setDraft(next)
    setDirty(true)
    setValidation(null)
    if (!jsonDirty) {
      setJsonText(prettyJson(next))
      setJsonError(null)
    }
  }

  const patchNode = (next: StepNode): void => {
    if (!draft) return
    applyDraft({ ...draft, steps: draft.steps.map((step) => (step.id === next.id ? next : step)) })
  }

  const addNode = (type: NodeType): void => {
    if (!draft) return
    const step = createStep(type, draft.steps.map((item) => item.id))
    const steps = [...draft.steps]
    const insertAt = selectedIndex >= 0 ? selectedIndex + 1 : steps.length
    steps.splice(insertAt, 0, step)
    applyDraft({ ...draft, steps })
    setSelectedId(step.id)
  }

  const moveNode = (nodeId: string, direction: -1 | 1): void => {
    if (!draft) return
    const index = draft.steps.findIndex((step) => step.id === nodeId)
    const targetIndex = index + direction
    if (index < 0 || targetIndex < 0 || targetIndex >= draft.steps.length) return
    const steps = [...draft.steps]
    const temp = steps[index]
    steps[index] = steps[targetIndex]
    steps[targetIndex] = temp
    applyDraft({ ...draft, steps })
  }

  const deleteNode = (nodeId: string): void => {
    if (!draft || draft.steps.length <= 1) return
    const index = draft.steps.findIndex((step) => step.id === nodeId)
    const steps = draft.steps.filter((step) => step.id !== nodeId)
    applyDraft({ ...draft, steps })
    if (selectedId === nodeId) {
      const nextSelected = steps[Math.min(Math.max(index, 0), steps.length - 1)]
      setSelectedId(nextSelected?.id ?? null)
    }
  }

  const toggleNodeEnabled = (nodeId: string, enabled: boolean): void => {
    if (!draft) return
    applyDraft({
      ...draft,
      steps: draft.steps.map((step) => (step.id === nodeId ? { ...step, enabled } : step)),
    })
  }

  const handleJsonChange = (text: string): void => {
    setJsonText(text)
    setJsonDirty(true)
    const parsed = parseJsonToData(text)
    setJsonError(parsed.error ?? null)
  }

  const handleFormat = (): void => {
    const parsed = parseJsonToData(jsonText)
    if (parsed.error || !parsed.data) {
      setJsonError(parsed.error ?? 'JSON 无效')
      return
    }
    setJsonText(prettyJson(parsed.data))
    setJsonError(null)
  }

  const handleApplyJson = (): void => {
    const parsed = parseJsonToData(jsonText)
    if (parsed.error || !parsed.data) {
      setJsonError(parsed.error ?? 'JSON 无效')
      message.error(`JSON 无效：${parsed.error ?? '未知错误'}`)
      return
    }
    const next = normalizeDraft(parsed.data)
    setDraft(next)
    setDirty(true)
    setJsonDirty(false)
    setJsonText(prettyJson(next))
    setJsonError(null)
    setValidation(null)
    if (!next.steps.some((step) => step.id === selectedId)) {
      setSelectedId(next.steps[0]?.id ?? null)
    }
    message.success('已应用到表单')
  }

  /** 保存/校验前取有效数据：JSON 有改动且能解析时以源码为准。 */
  const effectiveData = (): ScriptData | null => {
    if (!draft) return null
    if (!jsonDirty) return draft
    const parsed = parseJsonToData(jsonText)
    if (parsed.error || !parsed.data) {
      const errorText = parsed.error ?? 'JSON 无效'
      setJsonError(errorText)
      message.error(`JSON 无效：${errorText}`)
      return null
    }
    const next = normalizeDraft(parsed.data)
    setDraft(next)
    setJsonDirty(false)
    setJsonText(prettyJson(next))
    setJsonError(null)
    return next
  }

  const handleSave = async (): Promise<void> => {
    const data = effectiveData()
    if (!data) return
    if (data.steps.length === 0) {
      message.warning('脚本至少需要一个节点')
      return
    }
    try {
      await updateMutation.mutateAsync({ name: data.name, notes: data.notes, data })
      setDirty(false)
      setValidation(null)
      message.success('脚本已保存')
    } catch (error) {
      const errors = validationErrorsOf(error)
      if (errors.length > 0) setValidation({ valid: false, errors, warnings: [] })
    }
  }

  const handleValidate = async (): Promise<void> => {
    const data = effectiveData()
    if (!data) return
    try {
      const result = await validateMutation.mutateAsync({ data })
      setValidation(result)
      if (result.valid) message.success('校验通过')
    } catch (error) {
      const errors = validationErrorsOf(error)
      if (errors.length > 0) setValidation({ valid: false, errors, warnings: [] })
    }
  }

  const jumpToPath = (path: string): void => {
    if (!draft) return
    const index = stepIndexFromPath(path)
    if (index === null) return
    const node = draft.steps[index]
    if (node) setSelectedId(node.id)
  }

  if (scriptQuery.isLoading) {
    return (
      <div className="page-fill">
        <Skeleton active paragraph={{ rows: 12 }} />
      </div>
    )
  }

  if (scriptQuery.isError) {
    return (
      <Result
        status="warning"
        title="脚本加载失败"
        subTitle={describeApiError(scriptQuery.error)}
        extra={
          <Space>
            <Button onClick={() => void scriptQuery.refetch()}>重试</Button>
            <Button type="primary" onClick={() => navigate('/scripts')}>
              返回脚本列表
            </Button>
          </Space>
        }
      />
    )
  }

  if (!draft) {
    return (
      <div className="page-fill">
        <Skeleton active paragraph={{ rows: 12 }} />
      </div>
    )
  }

  return (
    <div className="page-fill">
      <div className="page-toolbar">
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/scripts')}>
            返回
          </Button>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {draft.name}
          </Typography.Title>
          {dirty ? <Tag color="orange">未保存</Tag> : <Tag color="green">已保存</Tag>}
          <Typography.Text type="secondary" className="small-text">
            {draft.steps.length} 节点 · id {id}
          </Typography.Text>
        </Space>
        <Space wrap>
          <Button
            icon={<CheckCircleOutlined />}
            loading={validateMutation.isPending}
            onClick={() => void handleValidate()}
          >
            校验
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={updateMutation.isPending}
            disabled={!dirty && !jsonDirty}
            onClick={() => void handleSave()}
          >
            保存
          </Button>
          <Button icon={<PlayCircleOutlined />} onClick={() => setRunOpen(true)}>
            运行
          </Button>
        </Space>
      </div>

      <div className="editor-layout">
        <div className="editor-col editor-left">
          <NodeList
            steps={draft.steps}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAdd={addNode}
            onMove={moveNode}
            onDelete={deleteNode}
            onToggleEnabled={toggleNodeEnabled}
          />
        </div>

        <div className="editor-center">
          <div className="editor-center-card" style={{ padding: 0 }}>
            <Collapse
              size="small"
              activeKey={settingsOpen ? ['settings'] : []}
              onChange={(keys) => {
                const list = Array.isArray(keys) ? keys : [keys]
                setSettingsOpen(list.length > 0)
              }}
              items={[
                {
                  key: 'settings',
                  label: '脚本设置（名称 / 变量 / 默认超时 / logcat）',
                  children: <ScriptSettingsForm data={draft} onChange={applyDraft} />,
                },
              ]}
            />
          </div>
          <div className="editor-center-card">
            {selectedNode ? (
              <NodeForm
                node={selectedNode}
                index={selectedIndex}
                total={draft.steps.length}
                steps={draft.steps}
                onChange={patchNode}
              />
            ) : (
              <Empty description="选择左侧节点进行编辑，或新增一个节点" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </div>
        </div>

        <div className="editor-col editor-right">
          <JsonSourcePanel
            text={jsonText}
            dirty={jsonDirty}
            parseError={jsonError}
            validation={validation}
            onChangeText={handleJsonChange}
            onFormat={handleFormat}
            onApply={handleApplyJson}
            onJumpPath={jumpToPath}
          />
        </div>
      </div>

      {id ? (
        <RunScriptModal
          open={runOpen}
          scriptId={id}
          scriptName={draft.name}
          onClose={() => setRunOpen(false)}
        />
      ) : null}
    </div>
  )
}
