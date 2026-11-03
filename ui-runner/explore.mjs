import fs from 'node:fs'
import readline from 'node:readline'
import { chromium, firefox, webkit } from 'playwright'
import { PlaywrightAgent } from '@midscene/web/playwright'
import {
  detectRepeatedInputWarns,
  isFailedTask,
  isSuccessTask,
  planningItem,
  progressKeysFromDump,
  resumeTask,
  yamlItem,
} from './explore_progress.mjs'

const LOG_PREFIX = 'RAINAHUB_LOG '
const RESULT_PREFIX = 'RAINAHUB_RESULT '
const NEED_HUMAN_PREFIX = 'RAINAHUB_NEED_HUMAN '
const RESUME_PREFIX = 'RAINAHUB_RESUME '
const ABORT_PREFIX = 'RAINAHUB_ABORT'
const DEFAULT_REPLAN_LIMIT = 20
const FAIL_STREAK_LIMIT = 4
const MAX_HUMAN_ASSISTS = 3
const HUMAN_HINT_HEADED =
  `连续 ${FAIL_STREAK_LIMIT} 次识别或操作失败。浏览器保持打开：请在页面上手动操作，或在对话中输入验证码/说明后发送；也可发送「继续」。`
const HUMAN_HINT_HEADLESS =
  `连续 ${FAIL_STREAK_LIMIT} 次识别或操作失败，已停止探索。请改用有头模式以便人工协助，或修改描述后重试。`
const CYCLE_LIMIT_HINT_HEADED =
  '规划轮次已用尽仍未完成。浏览器保持打开：请在页面上手动操作，或在对话中补充说明后发送「继续」。'
const CYCLE_LIMIT_HINT_HEADLESS =
  '规划轮次已用尽仍未完成，已停止探索。请改用有头模式以便人工协助，或修改描述后重试。'

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })

function readLine() {
  return new Promise((resolve) => {
    const onLine = (line) => {
      cleanup()
      resolve(String(line || ''))
    }
    const onClose = () => {
      cleanup()
      resolve(null)
    }
    const cleanup = () => {
      rl.off('line', onLine)
      rl.off('close', onClose)
    }
    rl.once('line', onLine)
    rl.once('close', onClose)
  })
}

function writeLine(prefix, payload) {
  fs.writeSync(1, `${prefix}${JSON.stringify(payload)}\n`)
}

function log(message) {
  writeLine(LOG_PREFIX, { message: String(message || '').slice(0, 500) })
}

function emit(payload) {
  writeLine(RESULT_PREFIX, payload)
}

function emitNeedHuman(reason, hint) {
  writeLine(NEED_HUMAN_PREFIX, {
    reason: String(reason || '').slice(0, 500),
    hint: String(hint || '').slice(0, 500),
  })
}

function actionsFromDump(dump) {
  const yamlActions = []
  const planned = []
  const executions = Array.isArray(dump?.executions) ? dump.executions : []
  for (const execution of executions) {
    const tasks = Array.isArray(execution?.tasks) ? execution.tasks : []
    for (const task of tasks) {
      const output = task?.output || {}
      const flows = Array.isArray(output.yamlFlow) ? output.yamlFlow : []
      for (const item of flows) {
        const converted = yamlItem(item)
        if (converted) yamlActions.push(converted)
      }
      const acts = Array.isArray(output.actions) ? output.actions : []
      for (const act of acts) {
        const converted = planningItem(act)
        if (converted) planned.push(converted)
      }
    }
  }
  return yamlActions.length ? yamlActions : planned
}

function createDumpProgressTracker(agent, { onFailStreak } = {}) {
  const seen = new Set()
  const seenFailKeys = new Set()
  const seenWarnKeys = new Set()
  let failStreak = 0
  let aborted = false

  const flush = () => {
    if (aborted) return
    try {
      const dump = agent.dump
      const executions = Array.isArray(dump?.executions) ? dump.executions : []
      for (let ei = 0; ei < executions.length; ei += 1) {
        const tasks = Array.isArray(executions[ei]?.tasks) ? executions[ei].tasks : []
        for (let ti = 0; ti < tasks.length; ti += 1) {
          const task = tasks[ti]
          const failKey = `fail:${ei}:${ti}:${String(task?.status || '')}:${String(task?.errorMessage || task?.error || '')}`
          if (isSuccessTask(task) && !seenFailKeys.has(`ok:${ei}:${ti}`)) {
            seenFailKeys.add(`ok:${ei}:${ti}`)
            failStreak = 0
          } else if (isFailedTask(task) && !seenFailKeys.has(failKey)) {
            seenFailKeys.add(failKey)
            failStreak += 1
            log(`识别/操作失败（连续 ${failStreak}/${FAIL_STREAK_LIMIT}）`)
            if (failStreak >= FAIL_STREAK_LIMIT && typeof onFailStreak === 'function') {
              aborted = true
              onFailStreak(failStreak)
              return
            }
          }
        }
      }
      for (const item of progressKeysFromDump(dump)) {
        if (seen.has(item.key)) continue
        seen.add(item.key)
        log(item.message)
      }
      for (const warn of detectRepeatedInputWarns(dump, seenWarnKeys)) {
        log(warn.message)
      }
    } catch {
      // dump may be mid-update
    }
  }
  return {
    flush,
    start() {
      flush()
      return setInterval(flush, 1000)
    },
  }
}

function isHumanAssistableError(ex, stopReason = '') {
  if (stopReason) return true
  const message = ex && ex.message ? String(ex.message) : String(ex)
  const reason =
    ex && typeof ex === 'object' && ex.reason != null
      ? String(ex.reason.message || ex.reason)
      : ''
  const combined = `${message}\n${reason}`
  if (/连续 \d+ 次识别或操作失败/.test(combined)) return true
  if (/planning cycle limit|replanningCycleLimit|MIDSCENE_REPLANNING_CYCLE_LIMIT/i.test(combined)) {
    return true
  }
  return false
}

function formatExploreError(ex, stopReason = '', headed = false) {
  if (stopReason) return stopReason
  const message = ex && ex.message ? String(ex.message) : String(ex)
  const reason =
    ex && typeof ex === 'object' && ex.reason != null
      ? String(ex.reason.message || ex.reason)
      : ''
  const combined = `${message}\n${reason}`
  if (/连续 \d+ 次识别或操作失败/.test(combined)) {
    return headed ? HUMAN_HINT_HEADED : HUMAN_HINT_HEADLESS
  }
  if (/planning cycle limit|replanningCycleLimit|MIDSCENE_REPLANNING_CYCLE_LIMIT/i.test(combined)) {
    return headed ? CYCLE_LIMIT_HINT_HEADED : CYCLE_LIMIT_HINT_HEADLESS
  }
  if (/TaskExecutionError/i.test(message)) {
    return message
  }
  return ex && ex.stack ? String(ex.stack) : message
}

async function waitHumanResume(reason, hint) {
  log(hint)
  emitNeedHuman(reason, hint)
  log('等待用户在对话中回复或手动操作后发送「继续」…')
  while (true) {
    const line = await readLine()
    if (line == null) {
      return { action: 'abort', text: '' }
    }
    const trimmed = line.trim()
    if (!trimmed) continue
    if (trimmed === ABORT_PREFIX || trimmed.startsWith(`${ABORT_PREFIX} `)) {
      return { action: 'abort', text: '' }
    }
    if (trimmed.startsWith(RESUME_PREFIX)) {
      let text = ''
      try {
        const payload = JSON.parse(trimmed.slice(RESUME_PREFIX.length) || '{}')
        text = String(payload?.text ?? '')
      } catch {
        text = trimmed.slice(RESUME_PREFIX.length)
      }
      return { action: 'resume', text }
    }
    // tolerate bare JSON resume payloads
    try {
      const payload = JSON.parse(trimmed)
      if (payload && (payload.action === 'abort' || payload.abort)) {
        return { action: 'abort', text: '' }
      }
      if (payload && (payload.action === 'resume' || payload.text != null)) {
        return { action: 'resume', text: String(payload.text ?? '') }
      }
    } catch {
      // ignore unknown lines
    }
  }
}

const jobLine = await readLine()
let job
try {
  job = JSON.parse(jobLine || '{}')
} catch (ex) {
  emit({ ok: false, error: `任务 JSON 无法解析: ${ex.message || ex}`, actions: [] })
  process.exit(1)
}

const headed = Boolean(job.headed)
const browserName = ['chromium', 'firefox', 'webkit'].includes(job.browser) ? job.browser : 'chromium'
const launcher = { chromium, firefox, webkit }[browserName]
const viewport = job.viewport || { width: 1280, height: 720 }
const baseTask = String(job.task || '完成页面上的操作')
const replanLimit = Math.max(
  1,
  Number(job.replanningCycleLimit) ||
    Number(process.env.MIDSCENE_REPLANNING_CYCLE_LIMIT) ||
    DEFAULT_REPLAN_LIMIT,
)

let browser
let pollTimer
let humanAssists = 0

try {
  log(`启动浏览器 ${browserName}${headed ? '（有头）' : '（无头）'}…`)
  browser = await launcher.launch({
    headless: !headed,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  })
  const context = await browser.newContext({
    baseURL: job.baseUrl,
    viewport: {
      width: Number(viewport.width) || 1280,
      height: Number(viewport.height) || 720,
    },
  })
  const page = await context.newPage()
  page.setDefaultTimeout(Number(job.timeoutMs) || 15000)
  const start = job.startPath && String(job.startPath).startsWith('/') ? job.startPath : `/${job.startPath || ''}`
  log(`打开页面 ${start}`)
  await page.goto(start, { waitUntil: 'domcontentloaded' })

  const agent = new PlaywrightAgent(page, {
    modelConfig: job.modelConfig || {},
    generateReport: false,
    autoPrintReportMsg: false,
    replanningCycleLimit: replanLimit,
  })

  let taskText = baseTask
  while (true) {
    let stopReason = ''
    const abort = new AbortController()
    log(
      `开始 Midscene aiAct（规划轮次上限 ${replanLimit}，连败 ${FAIL_STREAK_LIMIT} 次将暂停；人工协助 ${humanAssists}/${MAX_HUMAN_ASSISTS}）…`,
    )
    const progress = createDumpProgressTracker(agent, {
      onFailStreak() {
        stopReason = headed ? HUMAN_HINT_HEADED : HUMAN_HINT_HEADLESS
        log(stopReason)
        abort.abort()
      },
    })
    pollTimer = progress.start()
    try {
      await agent.aiAct(taskText, { abortSignal: abort.signal })
      clearInterval(pollTimer)
      pollTimer = undefined
      progress.flush()
      const actions = actionsFromDump(agent.dump)
      log(`aiAct 完成，解析到 ${actions.length} 步`)
      emit({ ok: true, actions, error: '' })
      break
    } catch (ex) {
      if (pollTimer) clearInterval(pollTimer)
      pollTimer = undefined
      const assistable = isHumanAssistableError(ex, stopReason)
      const errorText = formatExploreError(ex, stopReason, headed)
      if (!headed || !assistable || humanAssists >= MAX_HUMAN_ASSISTS) {
        emit({ ok: false, actions: actionsFromDump(agent.dump), error: errorText })
        process.exitCode = 1
        break
      }
      humanAssists += 1
      const hint = errorText
      const resume = await waitHumanResume(errorText, hint)
      if (resume.action !== 'resume') {
        emit({
          ok: false,
          actions: actionsFromDump(agent.dump),
          error: '已取消人工协助，探索结束。',
        })
        process.exitCode = 1
        break
      }
      taskText = resumeTask(baseTask, resume.text)
      log(`收到用户回复，继续探索（人工协助 ${humanAssists}/${MAX_HUMAN_ASSISTS}）…`)
    }
  }
} catch (ex) {
  if (pollTimer) clearInterval(pollTimer)
  emit({ ok: false, actions: [], error: formatExploreError(ex, '', headed) })
  process.exitCode = 1
} finally {
  if (pollTimer) clearInterval(pollTimer)
  rl.close()
  if (browser) await browser.close().catch(() => {})
}
