// 镜像模块的 REST 出口：统一走 fe-core 的 @/api/client（契约 §12：baseURL=/api、统一错误信封）。
//
// 这里只做两件事：
//   1) 再导出 get/post/del，供 features/mirror/** 使用（与 fe-core 约定的签名一致）；
//   2) 提供 errorMessage()：把 ApiError 转成给用户看的中文文案（复用 client 的 describeApiError）。

import { describeApiError, toApiError } from '@/api/client'

export { get, post, del } from '@/api/client'

/** 把任意异常转成可读文案（含错误码提示；网络错误给出后端未启动的排查提示）。 */
export function errorMessage(err: unknown): string {
  const apiError = toApiError(err)
  if (apiError.code === 'NETWORK') return apiError.message || '无法连接后端服务'
  return describeApiError(err)
}
