// 无 WebCodecs 浏览器的引导页（契约 §6.2-6：前端显示引导页，后端不做截图轮询降级）。
// 明确不给任何“截图轮询”降级入口；引导用户换新版 Chrome/Edge 或改走物理录制。

import { Button, Result, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

export function UnsupportedNotice() {
  const navigate = useNavigate()
  return (
    <Result
      status="warning"
      title="当前浏览器不支持 WebCodecs 镜像"
      subTitle="AutoTest 的实时镜像依赖 WebCodecs（VideoDecoder）。本浏览器未提供该能力，因此不提供截图轮询降级。"
      extra={
        <Space direction="vertical" size={12} align="center">
          <Typography.Text type="secondary">
            请使用新版 Chrome / Edge（Windows、macOS、Linux 均可），或改用「物理录制」模式在
            /recorder 页面操作（getevent 采集触摸，不依赖 WebCodecs）。
          </Typography.Text>
          <Space wrap>
            <Button type="primary" onClick={() => navigate('/recorder')}>
              前往物理录制
            </Button>
            <Button onClick={() => window.open('https://www.google.com/chrome/', '_blank', 'noopener')}>
              下载 Chrome
            </Button>
          </Space>
        </Space>
      }
    />
  )
}
