// 镜像工具条：只放“动作”，不放会变的文案（状态/分辨率/fps 全移到舞台下方的状态行，
// 这样操作时上方内容不会跳动）。

import { Button, Space } from 'antd'
import type { MirrorSession } from '../hooks/useMirrorSession'
import type { MirrorUiStatus } from '../types'

export interface MirrorToolbarProps {
  serial: string
  session: MirrorSession
  inputBusy?: boolean
}

const BUSY_STATUSES: MirrorUiStatus[] = ['checking', 'starting']

export function MirrorToolbar({ session }: MirrorToolbarProps) {
  const { status, isSupported } = session
  const busy = BUSY_STATUSES.includes(status)
  const running = status !== 'idle' && status !== 'starting'

  return (
    <Space wrap size={[8, 8]} className="mirror-toolbar">
      {status === 'idle' ? (
        <Button type="primary" disabled={!isSupported} onClick={() => void session.startMirror()}>
          启动镜像
        </Button>
      ) : (
        <Button danger onClick={() => void session.stopMirror()}>
          停止镜像
        </Button>
      )}
      <Button onClick={session.reconnect} disabled={!isSupported || busy}>
        重新连接
      </Button>
      <Button onClick={session.requestKeyframe} disabled={!running}>
        请求关键帧
      </Button>
    </Space>
  )
}
