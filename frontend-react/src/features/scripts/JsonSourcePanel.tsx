// JSON 源码视图（CodeMirror）：可编辑、格式化、应用到表单；展示校验错误并可跳节点
import { Alert, Button, Space, Tag, Typography } from 'antd'
import { CheckOutlined, FormatPainterOutlined } from '@ant-design/icons'
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'

import type { ScriptValidation } from '@/types/api'

export interface JsonSourcePanelProps {
  text: string
  dirty: boolean
  parseError: string | null
  validation: ScriptValidation | null
  onChangeText: (text: string) => void
  onFormat: () => void
  onApply: () => void
  onJumpPath: (path: string) => void
}

export default function JsonSourcePanel({
  text,
  dirty,
  parseError,
  validation,
  onChangeText,
  onFormat,
  onApply,
  onJumpPath,
}: JsonSourcePanelProps) {
  return (
    <div className="json-panel">
      <div className="editor-col-header">
        <Space size={6}>
          <Typography.Text strong>JSON 源码</Typography.Text>
          {dirty ? <Tag color="orange">未应用</Tag> : <Tag color="green">与表单同步</Tag>}
        </Space>
        <Space size={4}>
          <Button size="small" icon={<FormatPainterOutlined />} onClick={onFormat}>
            格式化
          </Button>
          <Button size="small" type="primary" ghost icon={<CheckOutlined />} onClick={onApply}>
            应用到表单
          </Button>
        </Space>
      </div>

      <div className="json-editor-host">
        <CodeMirror
          value={text}
          height="100%"
          style={{ height: '100%' }}
          theme="light"
          extensions={[json()]}
          onChange={(value) => onChangeText(value)}
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            highlightActiveLine: true,
            bracketMatching: true,
            autocompletion: false,
          }}
        />
      </div>

      {parseError ? (
        <Alert
          style={{ margin: 8 }}
          type="error"
          showIcon
          message="JSON 解析失败"
          description={<span className="code-text">{parseError}</span>}
        />
      ) : null}

      {validation ? (
        <div className="validation-list">
          <Space size={8} style={{ marginBottom: 4 }}>
            <Typography.Text strong className="small-text">
              校验结果
            </Typography.Text>
            <Tag color={validation.valid ? 'success' : 'error'}>{validation.valid ? '通过' : '未通过'}</Tag>
            <Typography.Text type="secondary" className="small-text">
              错误 {validation.errors.length} · 警告 {validation.warnings.length}
            </Typography.Text>
          </Space>
          {validation.errors.map((item, index) => (
            <div
              key={`error-${index}-${item.path}`}
              className="validation-item"
              onClick={() => onJumpPath(item.path)}
              title={item.path ? `点击跳转：${item.path}` : undefined}
            >
              <Tag color="error">错误</Tag>
              <span className="code-text">{item.path || 'script'}</span>
              <span>{item.message}</span>
            </div>
          ))}
          {validation.warnings.map((item, index) => (
            <div
              key={`warning-${index}-${item.path}`}
              className="validation-item"
              onClick={() => onJumpPath(item.path)}
              title={item.path ? `点击跳转：${item.path}` : undefined}
            >
              <Tag color="warning">警告</Tag>
              <span className="code-text">{item.path || 'script'}</span>
              <span>{item.message}</span>
            </div>
          ))}
          {validation.errors.length === 0 && validation.warnings.length === 0 ? (
            <Typography.Text type="secondary" className="small-text">
              没有发现错误
            </Typography.Text>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
