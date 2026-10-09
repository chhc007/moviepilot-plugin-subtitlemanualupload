import { computed, ref } from 'vue'

const EMPTY_AUTO_TRANSFER_QUEUE = {
  summary: { total: 0, active: 0, pending: 0, in_progress: 0, completed: 0, skipped: 0, failed: 0 },
  tasks: [],
  rate_limits: {},
  season_package_cache: [],
}

export function createEmptyAutoTransferQueue() {
  return {
    summary: { ...EMPTY_AUTO_TRANSFER_QUEUE.summary },
    tasks: [],
    rate_limits: {},
    season_package_cache: [],
  }
}

export function useAutoTransferQueue({
  pluginApi,
  unwrapResponse,
  errorMessage,
  error,
  message,
  selectedTargets,
  isLocked,
}) {
  const autoTransferQueue = ref(createEmptyAutoTransferQueue())
  const autoQueueDialog = ref(false)
  const autoQueueMutating = ref(false)
  const autoQueueEnqueueing = ref(false)
  const autoQueueActionTaskId = ref('')
  let autoQueueTimer = null

  const autoQueueSummary = computed(() => autoTransferQueue.value?.summary || {})
  const autoQueueTasks = computed(() => autoTransferQueue.value?.tasks || [])
  const autoQueueActive = computed(() => Number(autoQueueSummary.value.active || 0) > 0)
  const batchMatchTargets = computed(() => (selectedTargets?.value || []).filter(
    item => !isLocked?.(item.id) && item.writable !== false,
  ))
  const autoQueueSummaryText = computed(() => {
    const parts = []
    if (autoQueueSummary.value.in_progress) parts.push(`${autoQueueSummary.value.in_progress} 个处理中`)
    if (autoQueueSummary.value.pending) parts.push(`${autoQueueSummary.value.pending} 个排队`)
    if (autoQueueSummary.value.failed) parts.push(`${autoQueueSummary.value.failed} 个失败`)
    if (autoQueueSummary.value.completed) parts.push(`${autoQueueSummary.value.completed} 个完成`)
    if (autoQueueSummary.value.skipped) parts.push(`${autoQueueSummary.value.skipped} 个跳过`)
    return parts.length ? parts.join(' / ') : '暂无自动入库任务'
  })

  function applyAutoTransferSummary(summary) {
    autoTransferQueue.value = { ...autoTransferQueue.value, summary }
  }

  function stopAutoQueuePolling() {
    if (autoQueueTimer) {
      clearTimeout(autoQueueTimer)
      autoQueueTimer = null
    }
  }

  function scheduleAutoQueuePolling() {
    stopAutoQueuePolling()
    if (!autoQueueActive.value) return
    autoQueueTimer = setTimeout(() => {
      loadAutoTransferQueue()
    }, 3000)
  }

  async function loadAutoTransferQueue() {
    try {
      const response = await pluginApi.value.autoTransferQueue()
      autoTransferQueue.value = unwrapResponse(response) || autoTransferQueue.value
      scheduleAutoQueuePolling()
    } catch (err) {
      error.value = errorMessage(err, '读取自动入库队列失败')
    }
  }

  async function retryAutoTransferTask(task, options = {}) {
    if (!task?.id || autoQueueMutating.value) return
    const forceLowConfidence = Boolean(options.forceLowConfidence)
    if (forceLowConfidence && !window.confirm('确认无视智能调轴低可信结果，强制重新处理并直接入库？')) return
    autoQueueMutating.value = true
    autoQueueActionTaskId.value = task.id
    error.value = ''
    try {
      const response = await pluginApi.value.retryAutoTransferTask({
        task_id: task.id,
        force_low_confidence: forceLowConfidence,
      })
      autoTransferQueue.value = unwrapResponse(response) || autoTransferQueue.value
      message.value = response?.message || (forceLowConfidence ? '已强制重试自动入库任务' : '已重试自动入库任务')
      scheduleAutoQueuePolling()
    } catch (err) {
      error.value = errorMessage(err, '重试自动入库任务失败')
    } finally {
      autoQueueMutating.value = false
      autoQueueActionTaskId.value = ''
    }
  }

  async function clearAutoTransferHistory() {
    if (autoQueueMutating.value) return
    if (!window.confirm('确认清空已完成、已跳过和失败的自动入库历史任务？正在处理的任务会保留。')) return
    autoQueueMutating.value = true
    error.value = ''
    try {
      const response = await pluginApi.value.clearAutoTransferHistory()
      autoTransferQueue.value = unwrapResponse(response) || autoTransferQueue.value
      message.value = response?.message || '已清空自动入库历史任务'
    } catch (err) {
      error.value = errorMessage(err, '清空自动入库历史失败')
    } finally {
      autoQueueMutating.value = false
    }
  }

  function buildAutoTransferTargetPayload(target) {
    return {
      id: target.id,
      path: target.path,
      basename: target.basename,
      label: target.label,
      media_type: target.media_type,
      title: target.title,
      tmdb_id: target.tmdb_id,
      douban_id: target.douban_id,
      season: target.season,
      episode: target.episode,
      year: target.year,
      library_name: target.library_name,
      relative_path: target.relative_path,
      // 展示元数据（target_from_entry 已透传），随 target 一起回传，保证往返对称
      poster_url: target.poster_url,
      poster_thumb_url: target.poster_thumb_url,
      date: target.date,
      storage: target.storage,
      writable: target.writable,
      original_language: target.original_language,
      origin_country: target.origin_country,
      production_countries: target.production_countries,
      original_title: target.original_title,
      original_name: target.original_name,
      en_title: target.en_title,
      tmdb_aliases: target.tmdb_aliases,
    }
  }

  async function enqueueAutoTransferTargets() {
    const usableTargets = batchMatchTargets.value
    if (!usableTargets.length || autoQueueEnqueueing.value) return
    const confirmed = window.confirm(
      `确认把选中的 ${usableTargets.length} 个目标重新提交到自动处理队列？\n\n` +
      '将触发在线搜索 → 下载 → 写盘全流程，会对目标目录写入字幕文件，且该操作不可撤销。\n' +
      '已在队列中的目标会自动跳过。',
    )
    if (!confirmed) return
    autoQueueEnqueueing.value = true
    error.value = ''
    message.value = ''
    try {
      const response = await pluginApi.value.enqueueAutoTransferTargets({
        targets: usableTargets.map(buildAutoTransferTargetPayload),
      })
      autoTransferQueue.value = unwrapResponse(response) || autoTransferQueue.value
      message.value = response?.message || `已提交 ${usableTargets.length} 个目标到自动处理队列`
      scheduleAutoQueuePolling()
    } catch (err) {
      error.value = errorMessage(err, '批量匹配字幕失败')
    } finally {
      autoQueueEnqueueing.value = false
    }
  }

  return {
    autoTransferQueue,
    autoQueueDialog,
    autoQueueMutating,
    autoQueueEnqueueing,
    autoQueueActionTaskId,
    autoQueueSummary,
    autoQueueTasks,
    autoQueueActive,
    autoQueueSummaryText,
    batchMatchTargets,
    applyAutoTransferSummary,
    stopAutoQueuePolling,
    scheduleAutoQueuePolling,
    loadAutoTransferQueue,
    retryAutoTransferTask,
    clearAutoTransferHistory,
    enqueueAutoTransferTargets,
  }
}
