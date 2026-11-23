// 运行控制卡：暂停/停止/单步；暂停时 resume-check + 恢复/继续；失败/暂停后从节点重跑
import { useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { StepForwardOutlined, StopOutlined, PauseCircleOutlined, PlayCircleOutlined, RedoOutlined } from '@ant-design/icons'

import {
  useDebugRun,
  usePauseRun,
  useResumeCheck,
  useResumeRun,
  useStopRun,
  type ResumeCheckResult,
} from '@/api/runs'
import type { RunDetail, Script } from '@/types/api'
import { prettyJson } from '@/lib/format'

export interface RunControlsProps {
  run: RunDetail
  liveStatus: string
  script?: Script
}

const RUN_FROM_STATUSES = ['failed', 'stopped', 'interrupted', 'aborted', 'paused']

function ResumeCheckContent({ data }: { data: ResumeCheckResult | null }) {
  if (!data) return null
  const warnings = Array.isArray(data.warnings) ? data.warnings : []
  const resumeKind = data.resume_kind ?? data.kind
  return (
    <Space direction="vertical" style={{ width: '100%' }} size={10}>
      {resumeKind !== undefined ? (
        <div>
          恢复方式：<Tag color="blue">{String(resumeKind)}</Tag>
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          message="恢复前确认（resume-check）"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {warnings.map((warning, index) => (
                <li key={index}>{typeof warning === 'string' ? warning : prettyJson(warning)}</li>
              ))}
            </ul>
          }
        />
      ) : (
        <Alert type="success" showIcon message="检查通过，可以恢复（hot resume）" />
      )}
      {data.message !== undefined && data.message !== null ? (
        <Typography.Text>{String(data.message)}</Typography.Text>
      ) : null}
      <pre className="pre-block resume-check-pre">{prettyJson(data)}</pre>
    </Space>
  )
}

export default function RunControls({ run, liveStatus, script }: RunControlsProps) {
  const { message } = App.useApp()
  const pauseMutation = usePauseRun(run.id)
  const resumeCheckMutation = useResumeCheck(run.id)
  const resumeMutation = useResumeRun(run.id)
  const stopMutation = useStopRun(run.id)
  const debugMutation = useDebugRun(run.id)

  const [checkOpen, setCheckOpen] = useState(false)
  const [checkResult, setCheckResult] = useState<ResumeCheckResult | null>(null)
  const [runFromStepId, setRunFromStepId] = useState<string | undefined>(run.steps.find((step) => step.status === 'failed')?.step_id)

  const status = liveStatus || run.status
  const safetyOf = (stepId: string): string =>
    script?.data?.steps?.find((step) => step.id === stepId)?.rerun_safety ?? 'safe'

  const stepOptions = run.steps.map((step, index) => {
    const safety = safetyOf(step.step_id)
    const destructive = safety === 'destructive'
    return {
      value: step.step_id,
      label: `#${index + 1} ${step.step_name || step.step_id}（${step.step_type}${destructive ? ' · destructive' : ''}）`,
      disabled: destructive,
    }
  })

  const canRunFrom = RUN_FROM_STATUSES.includes(status) && stepOptions.length > 0
  const isPaused = status === 'paused'
  const isRunning = status === 'running'

  const handleResumeCheck = async (): Promise<void> => {
    try {
      const result = await resumeCheckMutation.mutateAsync()
      setCheckResult(result)
      setCheckOpen(true)
    } catch {
      // 全局错误提示
    }
  }

  const handleConfirmResume = async (): Promise<void> => {
    try {
      await resumeMutation.mutateAsync({ ack: true })
      setCheckOpen(false)
      message.success('已请求恢复运行')
    } catch {
      // 全局错误提示
    }
  }

  const handleRunFrom = (): void => {
    if (!runFromStepId) {
      message.warning('请选择要重跑的节点')
      return
    }
    debugMutation.mutate(
      { action: 'run_from', step_id: runFromStepId },
      { onSuccess: () => message.success(`已请求从节点 ${runFromStepId} 重跑`) },
    )
  }

  return (
    <Card
      size="small"
      title="运行控制"
      extra={<Tag color={isPaused ? 'warning' : isRunning ? 'processing' : 'default'}>当前：{status}</Tag>}
    >
      <Space wrap size={8}>
        {isRunning ? (
          <Button
            icon={<PauseCircleOutlined />}
            loading={pauseMutation.isPending}
            onClick={() =>
              pauseMutation.mutate(undefined, { onSuccess: () => message.success('已请求暂停') })
            }
          >
            暂停
          </Button>
        ) : null}

        {isRunning ? (
          <Tooltip title="按 debug 单步执行到下一节点前暂停（需后端 controls 支持）">
            <Button
              icon={<StepForwardOutlined />}
              loading={debugMutation.isPending}
              onClick={() =>
                debugMutation.mutate(
                  { action: 'step' },
                  { onSuccess: () => message.success('已请求单步执行') },
                )
              }
            >
              单步执行
            </Button>
          </Tooltip>
        ) : null}

        {isPaused ? (
          <>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={resumeCheckMutation.isPending}
              onClick={() => void handleResumeCheck()}
            >
              恢复（先检查）
            </Button>
            <Button
              icon={<StepForwardOutlined />}
              loading={debugMutation.isPending}
              onClick={() =>
                debugMutation.mutate(
                  { action: 'continue' },
                  { onSuccess: () => message.success('已请求继续执行') },
                )
              }
            >
              继续执行
            </Button>
          </>
        ) : null}

        {['running', 'queued', 'paused', 'stopping'].includes(status) ? (
          <Popconfirm
            title="停止运行？"
            description="正在执行的动作最多等待 5s 后强制中断"
            okText="停止"
            cancelText="取消"
            onConfirm={() =>
              stopMutation.mutate(undefined, { onSuccess: () => message.success('已请求停止') })
            }
          >
            <Button danger icon={<StopOutlined />} loading={stopMutation.isPending}>
              停止
            </Button>
          </Popconfirm>
        ) : null}
      </Space>

      {isPaused ? (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="运行已暂停（干预模式）"
          description="点击「恢复（先检查）」会请求 resume-check，确认无 warning 后 hot resume；也可以选择从某个节点重跑。"
        />
      ) : null}

      {canRunFrom ? (
        <div style={{ marginTop: 12 }}>
          <Space wrap align="center">
            <Typography.Text strong>从节点重跑</Typography.Text>
            <Select
              style={{ minWidth: 320 }}
              placeholder="选择起始节点（destructive 节点禁用）"
              value={runFromStepId}
              onChange={(value) => setRunFromStepId(value)}
              options={stepOptions}
              showSearch
              optionFilterProp="label"
            />
            <Button icon={<RedoOutlined />} loading={debugMutation.isPending} onClick={handleRunFrom}>
              从该节点重跑
            </Button>
          </Space>
          <div className="small-text muted" style={{ marginTop: 4 }}>
            run_from 会从指定节点继续执行；rerun_safety=destructive 的节点不允许作为重跑起点。
          </div>
        </div>
      ) : null}

      <Modal
        open={checkOpen}
        title="恢复前检查（resume-check）"
        okText="确认恢复"
        cancelText="取消"
        confirmLoading={resumeMutation.isPending}
        onOk={() => void handleConfirmResume()}
        onCancel={() => setCheckOpen(false)}
        width={640}
      >
        <ResumeCheckContent data={checkResult} />
      </Modal>
    </Card>
  )
}
