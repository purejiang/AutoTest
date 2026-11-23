// 批量删除结果提示：一次请求内单条失败不阻塞其他条，toast 必须汇总所有失败项。
import { ElMessage } from 'element-plus'

import type { BulkDeleteResult } from '@/types/api'

export type BulkId = string | number

export function bulkFailureText<TId extends BulkId>(
  result: BulkDeleteResult<TId>,
  label: string,
): string {
  const failed = Array.isArray(result.failed) ? result.failed : []
  if (failed.length === 0) return ''
  const details = failed.map((item) => {
    const code = item.code ? `[${item.code}] ` : ''
    const message = item.message || '删除失败'
    return `#${item.id} ${code}${message}`
  })
  return `已删除 ${result.deleted} 条${label}；${failed.length} 条失败：${details.join('；')}`
}

/** 成功/部分失败都给出汇总；失败列表全部拼接，不只展示第一条。 */
export function notifyBulkDeleteResult<TId extends BulkId>(
  result: BulkDeleteResult<TId>,
  label: string,
): void {
  const failed = Array.isArray(result.failed) ? result.failed : []
  if (failed.length > 0) {
    const options = {
      message: bulkFailureText(result, label),
      duration: 8000,
      grouping: true,
    }
    if (result.deleted === 0) ElMessage.error(options)
    else ElMessage.warning(options)
    return
  }
  if (result.deleted > 0) {
    ElMessage.success(`已删除 ${result.deleted} 条${label}`)
    return
  }
  ElMessage.info(`没有可删除的${label}`)
}
