import { chromium, firefox, webkit } from 'playwright'
import { expect } from '@playwright/test'
import { PlaywrightAgent } from '@midscene/web/playwright'

const LOG_PREFIX = 'RAINAHUB_LOG '

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = []
    process.stdin.on('data', (chunk) => chunks.push(chunk))
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    process.stdin.on('error', reject)
  })
}

function emit(payload) {
  process.stdout.write(`RAINAHUB_RESULT ${JSON.stringify(payload)}\n`)
}

function emitLog(message) {
  const msg = String(message || '').trim().slice(0, 500)
  if (!msg) return
  process.stdout.write(`${LOG_PREFIX}${JSON.stringify({ message: msg })}\n`)
}

function sameHost(base, current) {
  if (!current || current === 'about:blank') return
  const baseUrl = new URL(base)
  const currentUrl = new URL(current)
  if (baseUrl.host !== currentUrl.host) {
    throw new Error(`URL 主机不一致: ${current}`)
  }
}

async function captureShot(page) {
  let screenshotBase64 = ''
  let url = ''
  try {
    if (page && !page.isClosed()) {
      url = page.url() || ''
      const buf = await page.screenshot({ fullPage: false })
      screenshotBase64 = Buffer.from(buf).toString('base64')
    }
  } catch {
    // keep going even if screenshot fails
  }
  return { screenshotBase64, url }
}

const PAGE_STEP_ACTIONS = new Set([
  'goto',
  'click',
  'fill',
  'type',
  'press',
  'check',
  'uncheck',
  'selectOption',
  'hover',
  'reload',
])
const LOCATOR_STEP_ACTIONS = new Set([
  'click',
  'fill',
  'type',
  'press',
  'check',
  'uncheck',
  'selectOption',
  'hover',
])

function emitStep(payload) {
  process.stdout.write(`RAINAHUB_STEP ${JSON.stringify(payload)}\n`)
}

function wrapLocator(loc, { page, selector, stepIndex }) {
  return new Proxy(loc, {
    get(target, prop, receiver) {
      if (prop === 'first' || prop === 'last') {
        const next = Reflect.get(target, prop, receiver)
        return wrapLocator(next, { page, selector, stepIndex })
      }
      if (prop === 'nth') {
        return (index) => wrapLocator(target.nth(index), { page, selector, stepIndex })
      }
      const value = Reflect.get(target, prop, receiver)
      if (typeof value !== 'function' || !LOCATOR_STEP_ACTIONS.has(prop)) {
        return typeof value === 'function' ? value.bind(target) : value
      }
      return async (...args) => {
        const idx = stepIndex.value++
        const t0 = Date.now()
        const action = prop === 'selectOption' ? 'select_option' : prop
        try {
          const result = await value.apply(target, args)
          const { screenshotBase64, url } = await captureShot(page)
          emitStep({
            order: idx,
            action,
            selector: selector || '',
            status: 'passed',
            error: '',
            durationMs: Date.now() - t0,
            screenshotBase64,
            url,
          })
          return result
        } catch (ex) {
          const { screenshotBase64, url } = await captureShot(page)
          const error = ex && ex.message ? String(ex.message) : String(ex)
          const expectFail = /expect\s*\(|toHaveURL|toBeVisible|toHaveText|toBeHidden/.test(error)
          emitStep({
            order: idx,
            action,
            selector: selector || '',
            status: expectFail ? 'failed' : 'error',
            error,
            durationMs: Date.now() - t0,
            screenshotBase64,
            url,
          })
          throw ex
        }
      }
    },
  })
}

function instrumentPage(page, stepIndex) {
  return new Proxy(page, {
    get(target, prop, receiver) {
      if (prop === 'locator') {
        return (...args) => {
          const loc = target.locator(...args)
          const selector = args[0] != null ? String(args[0]) : ''
          return wrapLocator(loc, { page: target, selector, stepIndex })
        }
      }
      if (prop === 'getByRole') {
        return (...args) => {
          const loc = target.getByRole(...args)
          const role = args[0] != null ? String(args[0]) : 'role'
          const opts = args[1] && typeof args[1] === 'object' ? args[1] : {}
          const name = opts.name != null ? ` name=${opts.name}` : ''
          return wrapLocator(loc, { page: target, selector: `role=${role}${name}`, stepIndex })
        }
      }
      if (prop === 'getByText') {
        return (...args) => {
          const loc = target.getByText(...args)
          const text = args[0] != null ? String(args[0]) : ''
          return wrapLocator(loc, { page: target, selector: `text=${text}`, stepIndex })
        }
      }
      if (prop === 'getByLabel') {
        return (...args) => {
          const loc = target.getByLabel(...args)
          const text = args[0] != null ? String(args[0]) : ''
          return wrapLocator(loc, { page: target, selector: `label=${text}`, stepIndex })
        }
      }
      if (prop === 'getByPlaceholder') {
        return (...args) => {
          const loc = target.getByPlaceholder(...args)
          const text = args[0] != null ? String(args[0]) : ''
          return wrapLocator(loc, { page: target, selector: `placeholder=${text}`, stepIndex })
        }
      }
      const value = Reflect.get(target, prop, receiver)
      if (typeof value !== 'function' || !PAGE_STEP_ACTIONS.has(prop)) {
        return typeof value === 'function' ? value.bind(target) : value
      }
      return async (...args) => {
        const idx = stepIndex.value++
        const t0 = Date.now()
        const action = prop === 'selectOption' ? 'select_option' : prop
        let selector = ''
        if (prop === 'goto' && args[0] != null) selector = String(args[0])
        else if (typeof args[0] === 'string') selector = args[0]
        try {
          const result = await value.apply(target, args)
          const { screenshotBase64, url } = await captureShot(target)
          emitStep({
            order: idx,
            action,
            selector,
            status: 'passed',
            error: '',
            durationMs: Date.now() - t0,
            screenshotBase64,
            url,
          })
          return result
        } catch (ex) {
          const { screenshotBase64, url } = await captureShot(target)
          const error = ex && ex.message ? String(ex.message) : String(ex)
          const expectFail = /expect\s*\(|toHaveURL|toBeVisible|toHaveText|toBeHidden/.test(error)
          emitStep({
            order: idx,
            action,
            selector,
            status: expectFail ? 'failed' : 'error',
            error,
            durationMs: Date.now() - t0,
            screenshotBase64,
            url,
          })
          throw ex
        }
      }
    },
  })
}

const raw = await readStdin()
let job
try {
  job = JSON.parse(raw || '{}')
} catch (ex) {
  emit({ ok: false, error: `任务 JSON 无法解析: ${ex.message || ex}` })
  process.exit(1)
}

const script = String(job.script || '')
if (!script.includes('async function run')) {
  emit({ ok: false, error: '脚本需要定义 async function run(page, agent)' })
  process.exit(1)
}

const browserName = ['chromium', 'firefox', 'webkit'].includes(job.browser) ? job.browser : 'chromium'
const launcher = { chromium, firefox, webkit }[browserName]
const viewport = job.viewport || { width: 1280, height: 720 }
let browser
let page
try {
  browser = await launcher.launch({
    headless: job.headless !== false,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  })
  const contextOptions = {
    baseURL: job.baseUrl,
    viewport: {
      width: Number(viewport.width) || 1280,
      height: Number(viewport.height) || 720,
    },
  }
  const storageStatePath = String(job.storageStatePath || '').trim()
  if (storageStatePath) {
    contextOptions.storageState = storageStatePath
  }
  const extraHeaders = job.extraHTTPHeaders
  if (extraHeaders && typeof extraHeaders === 'object') {
    contextOptions.extraHTTPHeaders = Object.fromEntries(
      Object.entries(extraHeaders).map(([k, v]) => [k, v == null ? '' : String(v)]),
    )
  }
  const context = await browser.newContext(contextOptions)
  const localStoragePairs = job.localStorage
  if (localStoragePairs && typeof localStoragePairs === 'object') {
    await context.addInitScript((pairs) => {
      for (const [k, v] of Object.entries(pairs || {})) {
        try {
          localStorage.setItem(k, String(v))
        } catch (e) {}
      }
    }, localStoragePairs)
  }
  page = await context.newPage()
  page.setDefaultTimeout(Number(job.timeoutMs) || 15000)
  page.on('console', (msg) => {
    const type = String(msg.type() || '')
    if (type !== 'error' && type !== 'warning') return
    const text = String(msg.text() || '').trim().slice(0, 400)
    if (!text) return
    emitLog(`console.${type}: ${text}`)
  })
  emitLog(`浏览器已启动 browser=${browserName}`)
  const replanLimit = Math.max(
    1,
    Number(job.replanningCycleLimit) ||
      Number(process.env.MIDSCENE_REPLANNING_CYCLE_LIMIT) ||
      20,
  )
  const agent = new PlaywrightAgent(page, {
    modelConfig: job.modelConfig || {},
    generateReport: false,
    autoPrintReportMsg: false,
    replanningCycleLimit: replanLimit,
  })
  const rawEnv = job.env && typeof job.env === 'object' ? job.env : {}
  const env = {
    username: '',
    password: '',
    user: '',
    passwd: '',
    LOGIN_USERNAME: '',
    LOGIN_PASSWORD: '',
    ...Object.fromEntries(
      Object.entries(rawEnv).map(([k, v]) => [k, v == null ? '' : String(v)]),
    ),
  }
  if (!env.user) env.user = env.username
  if (!env.passwd) env.passwd = env.password
  if (!env.LOGIN_USERNAME) env.LOGIN_USERNAME = env.username
  if (!env.LOGIN_PASSWORD) env.LOGIN_PASSWORD = env.password
  const stepIndex = { value: 0 }
  const trackedPage = instrumentPage(page, stepIndex)
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  const invoke = new AsyncFunction(
    'page',
    'agent',
    'env',
    'expect',
    `${script}\nreturn run(page, agent);`,
  )
  emitLog('开始执行脚本')
  await invoke(trackedPage, agent, env, expect)
  sameHost(job.baseUrl, page.url())
  const saveStorageStatePath = String(job.saveStorageStatePath || '').trim()
  if (saveStorageStatePath) {
    await context.storageState({ path: saveStorageStatePath })
  }
  const { screenshotBase64, url } = await captureShot(page)
  emitLog(`脚本执行完成 url=${url || page.url() || ''} steps=${stepIndex.value}`)
  emit({ ok: true, error: '', screenshotBase64, url, stepCount: stepIndex.value })
} catch (ex) {
  const error = ex && ex.message ? String(ex.message) : String(ex)
  const expectFail = /expect\s*\(|toHaveURL|toBeVisible|toHaveText|toBeHidden/.test(error)
  const { screenshotBase64, url } = await captureShot(page)
  emitLog(`脚本执行失败: ${error.slice(0, 400)}`)
  emit({
    ok: false,
    error,
    status: expectFail ? 'failed' : 'error',
    screenshotBase64,
    url,
  })
  process.exitCode = 1
} finally {
  if (browser) await browser.close().catch(() => {})
}
