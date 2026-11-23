// 运行详情 · 产物区：截图缩略图 gallery + 视频/日志下载
import { Empty, Image, Space, Tag, Tooltip, Typography } from 'antd'

import { artifactHref, artifactKindMeta, formatBytes } from '@/lib/format'
import type { Artifact } from '@/types/api'

export interface RunArtifactsCardProps {
  artifacts: Artifact[]
}

function ArtifactFileRow({ artifact }: { artifact: Artifact }) {
  const meta = artifactKindMeta(artifact.kind)
  const url = artifactHref(artifact)
  const name = artifact.name || artifact.path.split(/[\\/]/).pop() || String(artifact.id)
  return (
    <Space size={6} wrap style={{ width: '100%' }}>
      <Tag color={meta.color}>{meta.label}</Tag>
      <span className="code-text small-text" style={{ wordBreak: 'break-all' }}>
        {name}
      </span>
      <span className="small-text muted">{formatBytes(artifact.size)}</span>
      {url ? (
        <a href={url} download={artifact.name || undefined} target="_blank" rel="noreferrer">
          下载 / 查看
        </a>
      ) : (
        <Tooltip title="后端未返回 artifact.url，且无法从 path 推导 /artifacts 直链">
          <span className="small-text muted">无直链</span>
        </Tooltip>
      )}
    </Space>
  )
}

export default function RunArtifactsCard({ artifacts }: RunArtifactsCardProps) {
  const screenshots = artifacts.filter((artifact) => artifact.kind === 'screenshot')
  const videos = artifacts.filter((artifact) => artifact.kind === 'video' || artifact.kind === 'screenrecord')
  const logs = artifacts.filter((artifact) => ['logcat', 'hierarchy', 'network'].includes(artifact.kind))
  const others = artifacts.filter(
    (artifact) =>
      !screenshots.includes(artifact) &&
      !videos.includes(artifact) &&
      !logs.includes(artifact),
  )

  return (
    <div>
      <Space size={8} style={{ marginBottom: 8 }}>
        <Typography.Text strong>产物</Typography.Text>
        <Tag>{artifacts.length}</Tag>
      </Space>

      {artifacts.length === 0 ? (
        <Empty description="暂无产物" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {screenshots.length > 0 ? (
            <div>
              <Typography.Text type="secondary" className="small-text">
                截图（{screenshots.length}）
              </Typography.Text>
              <Image.PreviewGroup>
                <div className="artifact-grid" style={{ marginTop: 6 }}>
                  {screenshots.map((artifact) => {
                    const url = artifactHref(artifact)
                    const name = artifact.name || String(artifact.id)
                    return (
                      <div key={artifact.id} className="artifact-card">
                        {url ? (
                          <Image
                            src={url}
                            width={132}
                            height={88}
                            style={{ objectFit: 'cover', borderRadius: 6 }}
                            alt={name}
                          />
                        ) : (
                          <div
                            style={{
                              width: 132,
                              height: 88,
                              background: '#fafafa',
                              border: '1px dashed #d9d9d9',
                              borderRadius: 6,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              fontSize: 12,
                              color: 'rgba(0,0,0,.45)',
                            }}
                          >
                            无直链
                          </div>
                        )}
                        <div className="artifact-name" title={artifact.path}>
                          {name}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </Image.PreviewGroup>
            </div>
          ) : null}

          {videos.length > 0 ? (
            <div>
              <Typography.Text type="secondary" className="small-text">
                视频 / 录屏（{videos.length}）
              </Typography.Text>
              <Space direction="vertical" size={2} style={{ width: '100%', marginTop: 6 }}>
                {videos.map((artifact) => (
                  <ArtifactFileRow key={artifact.id} artifact={artifact} />
                ))}
              </Space>
            </div>
          ) : null}

          {logs.length > 0 ? (
            <div>
              <Typography.Text type="secondary" className="small-text">
                日志 / 层级（{logs.length}）
              </Typography.Text>
              <Space direction="vertical" size={2} style={{ width: '100%', marginTop: 6 }}>
                {logs.map((artifact) => (
                  <ArtifactFileRow key={artifact.id} artifact={artifact} />
                ))}
              </Space>
            </div>
          ) : null}

          {others.length > 0 ? (
            <div>
              <Typography.Text type="secondary" className="small-text">
                其他（{others.length}）
              </Typography.Text>
              <Space direction="vertical" size={2} style={{ width: '100%', marginTop: 6 }}>
                {others.map((artifact) => (
                  <ArtifactFileRow key={artifact.id} artifact={artifact} />
                ))}
              </Space>
            </div>
          ) : null}
        </Space>
      )}
    </div>
  )
}
