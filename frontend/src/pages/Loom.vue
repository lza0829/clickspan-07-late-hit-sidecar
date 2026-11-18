<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const overview = ref(null)
const sessions = ref([])
const details = ref({})
const sidecarRows = ref([])
const err = ref('')

async function load() {
  err.value = ''
  try {
    overview.value = await api.overview()
    sessions.value = await api.sessions()
    const map = {}
    for (const s of sessions.value) {
      map[s.id] = await api.session(s.id)
    }
    details.value = map
    sidecarRows.value = await api.sidecar()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="stats" v-if="overview">
      <div class="stat"><b>{{ overview.visitors }}</b><span>访客</span></div>
      <div class="stat"><b>{{ overview.sessions }}</b><span>会话</span></div>
      <div class="stat"><b>{{ overview.hits_primary }}</b><span>正式击点</span></div>
      <div class="stat stat-sidecar"><b>{{ overview.sidecar }}</b><span>迟到旁路</span></div>
      <div class="stat"><b>{{ overview.quarantine }}</b><span>隔离</span></div>
      <div class="stat"><b>{{ overview.idle_timeout_sec }}s</b><span>空闲超时</span></div>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <section class="panel">
      <h1>会话织机</h1>
      <p class="muted">基座按访客整段挂会话；空闲切窗由空桩功能题实现。无 open 且最近会话已 closed 的迟到击点不进织机，落到下方旁路条。</p>
      <div class="lane-list">
        <article v-for="s in sessions" :key="s.id" class="lane">
          <div class="lane-head">
            <div>
              <strong>{{ s.id }}</strong>
              <span class="muted"> · {{ s.visitor_id }} · {{ s.status }}</span>
            </div>
            <span class="mono">{{ s.hit_count }} 点 · {{ s.started_at }} → {{ s.ended_at || '…' }}</span>
          </div>
          <div class="beads" v-if="details[s.id]">
            <template v-for="(h, i) in details[s.id].hits" :key="h.id">
              <span v-if="i" class="connector" aria-hidden="true"></span>
              <span class="hit-bead">
                <strong>{{ h.event_name }}</strong>
                <span>{{ h.page_path }}</span>
              </span>
            </template>
          </div>
        </article>
      </div>
    </section>

    <section class="panel sidecar-panel">
      <h2>迟到旁路条 <span class="muted">（只读核证，不计入正式 hit_count）</span></h2>
      <p v-if="!sidecarRows.length" class="muted">暂无旁路击点。</p>
      <div v-else class="sidecar-list">
        <article v-for="r in sidecarRows" :key="r.id" class="sidecar-item">
          <div class="sidecar-head">
            <strong class="mono">{{ r.hit_id }}</strong>
            <span class="muted">{{ r.visitor_id }} · {{ r.event_ts }}</span>
            <span class="sidecar-hint">提示所属 closed 会话：{{ r.closed_session_id || '—' }}</span>
          </div>
          <div class="sidecar-payload mono">{{ r.event_name }} @ {{ r.page_path }} · 批次 {{ r.batch_id || '—' }}</div>
        </article>
      </div>
    </section>
  </div>
</template>
