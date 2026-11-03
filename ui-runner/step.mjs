/**
 * Single Midscene step on an existing Chromium via CDP.
 * Must not call browser.close() — the Python-owned browser must stay alive.
 */
import { chromium } from 'playwright'
import { PlaywrightAgent } from '@midscene/web/playwright'

const RESULT_PREFIX = 'RAINAHUB_RESULT '
const LOG_PREFIX = 'RAINAHUB_LOG '

function readStdinLine() {
  return new Promise((resolve, reject) => {
    const chunks = []
    process.stdin.on('data', (chunk) => chunks.push(chunk))
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    process.stdin.on('error', reject)
  })
}

function emit(payload) {
  process.stdout.write(`${RESULT_PREFIX}${JSON.stringify(payload)}\n`)
}

function log(message) {
  process.stdout.write(`${LOG_PREFIX}${JSON.stringify({ message: String(message || '').slice(0, 500) })}\n`)
}

const raw = await readStdinLine()
let job
try {
  job = JSON.parse((raw || '').split('\n')[0] || '{}')
} catch (ex) {
  emit({ ok: false, error: `任务 JSON 无法解析: ${ex.message || ex}` })
  process.exit(1)
}

const cdpEndpoint = String(job.cdpEndpoint || '').trim()
const action = String(job.action || 'click').toLowerCase()
const prompt = String(job.prompt || '').trim() || '目标控件'
const value = job.value == null ? '' : String(job.value)
const timeoutMs = Number(job.timeoutMs) || 15000

if (!cdpEndpoint) {
  emit({ ok: false, error: '缺少 cdpEndpoint' })
  process.exit(1)
}

let browser
try {
  log(`CDP 连接 ${cdpEndpoint}，执行 ${action}`)
  browser = await chromium.connectOverCDP(cdpEndpoint)
  const contexts = browser.contexts()
  if (!contexts.length) {
    throw new Error('CDP 浏览器没有 context')
  }
  const pages = contexts[0].pages()
  if (!pages.length) {
    throw new Error('CDP 浏览器没有打开的页面')
  }
  const page = pages[pages.length - 1]
  page.setDefaultTimeout(timeoutMs)
  const agent = new PlaywrightAgent(page, {
    modelConfig: job.modelConfig || {},
    generateReport: false,
    autoPrintReportMsg: false,
    replanningCycleLimit: 8,
  })
  let text = ''
  if (action === 'query' || action === 'ai_query' || action === 'aiquery') {
    const rawText = await agent.aiQuery(prompt)
    text = String(rawText == null ? '' : rawText).replace(/[^a-zA-Z0-9]/g, '')
    log(`aiQuery 完成：${text ? `${text.slice(0, 4)}${'*'.repeat(Math.max(0, text.length - 4))}` : '(空)'}`)
    emit({ ok: true, error: '', action: 'query', prompt, text })
  } else if (action === 'fill' || action === 'input' || action === 'type') {
    await agent.aiInput(value, prompt)
    log(`${action} 完成`)
    emit({ ok: true, error: '', action, prompt })
  } else {
    await agent.aiTap(prompt)
    log(`${action} 完成`)
    emit({ ok: true, error: '', action, prompt })
  }
  // Disconnect without closing the remote browser.
  process.exit(0)
} catch (ex) {
  emit({ ok: false, error: String(ex && ex.message ? ex.message : ex).slice(0, 2000) })
  process.exit(1)
}
