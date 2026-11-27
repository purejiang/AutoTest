<script setup lang="ts">
// 设置 → 执行：镜像画质（MirrorConfigCard）+ 运行节奏 / 录制定位 / 采集 / 访问安全。
import MirrorConfigCard from '../MirrorConfigCard.vue'
import RestartHint from '../RestartHint.vue'
import SettingField from '../SettingField.vue'
import { useSettingsDraftContext } from '../useSettingsDraft'

const { values, sources, schemaByKey, entriesFor, sourceOf, configFile, setValue, deleteOverride, clearKey, scrcpyVersion } =
  useSettingsDraftContext()
</script>

<template>
  <div class="settings-pane">
    <MirrorConfigCard
      :values="values"
      :sources="sources"
      :schema="schemaByKey"
      :server-version="scrcpyVersion"
      @change="setValue"
      @clear="clearKey"
    />

    <div class="exec-grid">
      <section class="card exec-card">
        <div class="card-head">
          <h2>运行节奏</h2>
          <span
            class="card-hint"
            title="轮询间隔、默认超时/停顿、单次运行上限与最大步数。停顿只作为新建脚本的初值。"
            >轮询 · 超时 · 停顿 · 步数</span
          >
        </div>
        <div class="card-body">
          <div class="field-row">
            <SettingField
              v-for="entry in entriesFor('engine')"
              :key="entry.key"
              :entry="entry"
              :model-value="values[entry.key]"
              :source="sourceOf(entry)"
              :readonly="entry.layer === 'bootstrap'"
              :config-file="configFile"
              @update:model-value="setValue(entry.key, $event)"
              @clear="deleteOverride(entry)"
            />
          </div>
        </div>
      </section>

      <section class="card exec-card">
        <div class="card-head">
          <h2>录制与定位</h2>
          <span class="card-hint" title="点击前抓层级、录后确认与录制转脚本的定位方式。">层级抓取 · 定位方式</span>
        </div>
        <div class="card-body">
          <div class="field-row">
            <SettingField
              v-for="entry in entriesFor('recording')"
              :key="entry.key"
              :entry="entry"
              :model-value="values[entry.key]"
              :source="sourceOf(entry)"
              :readonly="entry.layer === 'bootstrap'"
              :config-file="configFile"
              @update:model-value="setValue(entry.key, $event)"
              @clear="deleteOverride(entry)"
            />
          </div>
        </div>
      </section>

      <section class="card exec-card">
        <div class="card-head">
          <h2>采集</h2>
          <span class="card-hint" title="logcat 进入运行产物；网络抓包只记录域名，并可在失败节点保存证据。">
            logcat · 域名抓包 · 失败证据
          </span>
        </div>
        <div class="card-body">
          <div class="field-row">
            <SettingField
              v-for="entry in entriesFor('capture')"
              :key="entry.key"
              :entry="entry"
              :model-value="values[entry.key]"
              :source="sourceOf(entry)"
              :readonly="entry.layer === 'bootstrap'"
              :config-file="configFile"
              @update:model-value="setValue(entry.key, $event)"
              @clear="deleteOverride(entry)"
            />
          </div>
        </div>
      </section>

      <section class="card exec-card">
        <div class="card-head">
          <h2>访问与安全</h2>
          <RestartHint detail="访问令牌修改后需要重启后端；对外暴露服务时建议设置。" />
        </div>
        <div class="card-body">
          <SettingField
            v-for="entry in entriesFor('security')"
            :key="entry.key"
            :entry="entry"
            :model-value="values[entry.key]"
            :source="sourceOf(entry)"
            :readonly="entry.layer === 'bootstrap'"
            :config-file="configFile"
            @update:model-value="setValue(entry.key, $event)"
            @clear="deleteOverride(entry)"
          />
        </div>
      </section>
    </div>
  </div>
</template>
