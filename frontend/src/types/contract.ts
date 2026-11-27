// 前后端契约的编译期护栏（不产生运行时代码）。
//
// 后端 OpenAPI 由 `scripts/export_openapi.py` 导出、`npm run types:gen` 生成
// `types/generated.ts`；这里检查手写领域类型与生成类型的关键一致性：
// 1) 后端有的字段，手写类型必须也有（字段改名/删除会直接编译失败）；
// 2) 后端字段可空（`T | null`）时，手写类型也必须允许 null（避免前端少判空）。
//
// 允许的差异：手写类型可以比后端更窄（如 `error` 用 `{code?, message?}` 代替 free-form dict）、
// 可以多出客户端补充字段。任何一侧漂移都会让 `npm run typecheck` 失败。
import type { Artifact, Recording, Run, RunDetail, Script, SettingEntry, SettingsResponse, StepResult } from './api'
import type { components } from './generated'

type Schema<K extends keyof components['schemas']> = components['schemas'][K]

/** 去掉索引签名（后端 `extra="allow"` 会生成 `& { [key: string]: unknown }`）。 */
type Plain<T> = { [K in keyof T as string extends K ? never : K]: T[K] }

/** 后端有、手写缺失的字段名。 */
type MissingKeys<Generated, Hand> = Exclude<keyof Generated, keyof Hand>

/** 后端字段可空、手写却没写 null 的字段名。 */
type NullMismatch<Generated, Hand> = {
  [K in keyof Generated]-?: K extends keyof Hand
    ? [null] extends [Generated[K]]
      ? [null] extends [Hand[K]]
        ? never
        : K
      : never
    : never
}[keyof Generated]

type Failures<Generated, Hand> = MissingKeys<Generated, Hand> | NullMismatch<Generated, Hand>

/** 兼容则 `true`；否则是失败字段名联合（赋值处报错并指出字段）。 */
type AssertCompatible<Generated, Hand> = [Failures<Generated, Hand>] extends [never]
  ? true
  : Failures<Generated, Hand>

export const runContractCheck: AssertCompatible<Plain<Schema<'RunOut'>>, Run> = true
export const stepContractCheck: AssertCompatible<Plain<Schema<'StepResultOut'>>, StepResult> = true
export const artifactContractCheck: AssertCompatible<Plain<Schema<'ArtifactOut'>>, Artifact> = true

// RunDetailOut = RunOut + steps/artifacts；先去掉数组字段，避免数组元素差异干扰标量检查。
type RunDetailScalars = Omit<Plain<Schema<'RunDetailOut'>>, 'steps' | 'artifacts'>
export const runDetailContractCheck: AssertCompatible<RunDetailScalars, RunDetail> = true

// 数组元素也要覆盖到：手写 RunDetail.steps/artifacts 必须与后端元素类型字段一致。
type DetailArraysOk = [
  MissingKeys<Plain<Schema<'StepResultOut'>>, StepResult>,
  MissingKeys<Plain<Schema<'ArtifactOut'>>, Artifact>,
] extends [never, never]
  ? true
  : false
export const runDetailArraysCheck: DetailArraysOk = true

// 设置页：后端 SettingsOut 与手写 SettingsResponse / SettingEntry 的字段与可空性对齐。
export const settingsContractCheck: AssertCompatible<Plain<Schema<'SettingsOut'>>, SettingsResponse> = true
export const settingEntryContractCheck: AssertCompatible<Plain<Schema<'SettingEntryOut'>>, SettingEntry> = true

// 脚本 / 录制：列表项与详情共用同一模型（RecordingOut / ScriptOut）。
export const scriptContractCheck: AssertCompatible<Plain<Schema<'ScriptOut'>>, Script> = true
export const recordingContractCheck: AssertCompatible<Plain<Schema<'RecordingOut'>>, Recording> = true
