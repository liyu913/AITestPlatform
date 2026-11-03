/** Progress log helpers for Midscene explore dump (testable, no side effects). */

export function shortText(value, max = 40) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/** Extract readable locate/prompt text; never String(object) → [object Object]. */
export function locateText(value) {
  if (value == null || value === '') return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value).trim()
  }
  if (typeof value !== 'object') return ''
  if (typeof value.promptDisplay === 'string' && value.promptDisplay.trim()) {
    return value.promptDisplay.trim()
  }
  if (typeof value.prompt === 'string' && value.prompt.trim()) {
    return value.prompt.trim()
  }
  if (value.prompt && typeof value.prompt === 'object') {
    const nested = locateText(value.prompt)
    if (nested) return nested
  }
  if (typeof value.description === 'string' && value.description.trim()) {
    return value.description.trim()
  }
  if (typeof value.text === 'string' && value.text.trim()) {
    return value.text.trim()
  }
  if (value.locate != null) {
    const nested = locateText(value.locate)
    if (nested) return nested
  }
  return ''
}

export function yamlItem(item) {
  if (!item || typeof item !== 'object') return null
  const locate = locateText(item.locate) || locateText(item.aiTap) || locateText(item.ai_tap)
  if (Object.prototype.hasOwnProperty.call(item, 'aiInput') || Object.prototype.hasOwnProperty.call(item, 'ai_input')) {
    const raw = Object.prototype.hasOwnProperty.call(item, 'aiInput') ? item.aiInput : item.ai_input
    return { kind: 'fill', locate, value: raw == null ? '' : String(raw) }
  }
  if (item.aiTap || item.ai_tap) {
    const name = locateText(item.aiTap) || locateText(item.ai_tap) || locate
    return { kind: 'click', locate: locate || name, name }
  }
  if (item.aiKeyboardPress) {
    return { kind: 'press', locate, value: String(item.aiKeyboardPress) }
  }
  return null
}

export function planningItem(act) {
  if (!act || typeof act !== 'object') return null
  const type = String(act.type || '').toLowerCase()
  const param = act.param && typeof act.param === 'object' ? act.param : {}
  const locate =
    locateText(act.locate) ||
    locateText(param.locate) ||
    locateText(param.prompt) ||
    locateText(param)
  const thought = shortText(act.thought || act.log || '', 80)
  if (type === 'input' || type === 'type' || type === 'fill') {
    return {
      kind: 'fill',
      locate,
      value: String(param.value ?? param.text ?? ''),
      thought,
    }
  }
  if (type === 'tap' || type === 'click') {
    return { kind: 'click', locate, thought }
  }
  return null
}

export function describeAction(action) {
  if (!action || typeof action !== 'object') return ''
  const locate = shortText(
    locateText(action.locate) || locateText(action.name) || locateText(action.selector) || '',
    80,
  )
  const thought = shortText(action.thought || '', 60)
  if (action.kind === 'click') {
    if (locate && thought) return `点击「${locate}」：${thought}`
    if (locate) return `点击「${locate}」`
    return thought ? `点击：${thought}` : '点击'
  }
  if (action.kind === 'fill') {
    const value = shortText(action.value, 20)
    if (locate && value) return `填写「${locate}」= ${value}`
    if (locate) return `填写「${locate}」`
    return value ? `填写 ${value}` : '填写'
  }
  if (action.kind === 'press') return `按键 ${shortText(action.value, 16) || ''}`
  if (action.kind === 'goto') return `打开 ${shortText(action.path || action.value, 60)}`
  return locate ? `${action.kind || '操作'}「${locate}」` : String(action.kind || '')
}

export function describeTaskTitle(task, ti) {
  const typeName = String(task?.type || task?.title || task?.name || `task-${ti}`)
  const subType = String(task?.subType || '').trim()
  const param = task?.param && typeof task.param === 'object' ? task.param : {}
  const locate = locateText(param.locate) || locateText(param.prompt) || locateText(param)
  const thought = shortText(task?.thought || '', 60)
  const value =
    param.value != null && param.value !== ''
      ? shortText(param.value, 20)
      : param.text != null && param.text !== ''
        ? shortText(param.text, 20)
        : ''

  if (/action\s*space/i.test(typeName) && subType) {
    let title = subType
    if (locate) title += `「${shortText(locate, 80)}」`
    if (value && !/tap|click/i.test(subType)) title += `=${value}`
    else if (thought && !locate) title += `：${thought}`
    return shortText(title, 120)
  }

  if (/planning/i.test(typeName)) {
    const logHint = shortText(task?.output?.log || thought || '', 80)
    if (logHint) return shortText(`Planning：${logHint}`, 120)
    return 'Planning'
  }

  if (subType && locate) return shortText(`${subType}「${locate}」`, 120)
  if (subType) return shortText(subType, 80)
  return shortText(typeName, 60)
}

/** True when task title already describes Input/Tap — skip yamlFlow duplicate lines. */
export function taskHasActionDetail(task, title) {
  const typeName = String(task?.type || task?.title || task?.name || '')
  const subType = String(task?.subType || '').trim()
  if (/action\s*space/i.test(typeName) && /^(input|tap|click|type|fill)/i.test(subType)) {
    return true
  }
  if (/^(input|tap|click)\b/i.test(String(title || ''))) {
    return true
  }
  return false
}

/**
 * Build progress log keys from Midscene dump.
 * When a task already has Action Space Input/Tap detail, do not also emit yamlFlow/actions rows.
 */
export function progressKeysFromDump(dump) {
  const keys = []
  const executions = Array.isArray(dump?.executions) ? dump.executions : []
  for (let ei = 0; ei < executions.length; ei += 1) {
    const execution = executions[ei]
    const tasks = Array.isArray(execution?.tasks) ? execution.tasks : []
    for (let ti = 0; ti < tasks.length; ti += 1) {
      const task = tasks[ti]
      const title = describeTaskTitle(task, ti)
      const status = shortText(task?.status || '', 20)
      keys.push({
        key: `task:${ei}:${ti}:${title}:${status}`,
        message: status ? `任务：${title}（${status}）` : `任务：${title}`,
      })
      if (taskHasActionDetail(task, title)) {
        continue
      }
      const output = task?.output || {}
      const flows = Array.isArray(output.yamlFlow) ? output.yamlFlow : []
      for (let fi = 0; fi < flows.length; fi += 1) {
        const converted = yamlItem(flows[fi])
        const desc = describeAction(converted)
        if (!desc) continue
        keys.push({ key: `flow:${ei}:${ti}:${fi}:${desc}`, message: desc })
      }
      const acts = Array.isArray(output.actions) ? output.actions : []
      for (let ai = 0; ai < acts.length; ai += 1) {
        const converted = planningItem(acts[ai])
        const desc = describeAction(converted)
        if (!desc) continue
        keys.push({ key: `act:${ei}:${ti}:${ai}:${desc}`, message: desc })
      }
    }
  }
  return keys
}

export function isFailedTask(task) {
  if (!task || typeof task !== 'object') return false
  const status = String(task.status || '').toLowerCase()
  if (status === 'failed' || status === 'error') return true
  if (task.errorMessage || task.error) return true
  return false
}

export function isSuccessTask(task) {
  if (!task || typeof task !== 'object') return false
  const status = String(task.status || '').toLowerCase()
  return status === 'finished' || status === 'success' || status === 'passed' || status === 'done'
}

export function isInputLikeTask(task) {
  const typeName = String(task?.type || '')
  const subType = String(task?.subType || '').trim()
  if (/action\s*space/i.test(typeName) && /^(input|type|fill)/i.test(subType)) return true
  if (/^input\b/i.test(subType)) return true
  return false
}

export function isClickLikeTask(task) {
  const typeName = String(task?.type || '')
  const subType = String(task?.subType || '').trim()
  if (/action\s*space/i.test(typeName) && /^(tap|click)/i.test(subType)) return true
  if (/^(tap|click)\b/i.test(subType)) return true
  return false
}

export function inputFingerprint(task) {
  const param = task?.param && typeof task.param === 'object' ? task.param : {}
  const locate = locateText(param.locate) || locateText(param.prompt) || locateText(param)
  const value =
    param.value != null && param.value !== ''
      ? String(param.value)
      : param.text != null && param.text !== ''
        ? String(param.text)
        : ''
  return `${locate}::${value}`
}

export const REPEAT_INPUT_WARN =
  '检测到相同输入重复填写且未点击登录/提交：应点击登录，或刷新验证码后再读图填写，勿重复同一串字符。'

/**
 * Scan dump for repeated successful Input (same locate+value) with no Click in between.
 * Returns warn messages for newly detected pairs (caller dedupes via seen set).
 */
export function detectRepeatedInputWarns(dump, seenWarnKeys = new Set()) {
  const warns = []
  const executions = Array.isArray(dump?.executions) ? dump.executions : []
  let lastInputFp = ''
  let lastInputKey = ''
  for (let ei = 0; ei < executions.length; ei += 1) {
    const tasks = Array.isArray(executions[ei]?.tasks) ? executions[ei].tasks : []
    for (let ti = 0; ti < tasks.length; ti += 1) {
      const task = tasks[ti]
      if (!isSuccessTask(task)) continue
      if (isClickLikeTask(task)) {
        lastInputFp = ''
        lastInputKey = ''
        continue
      }
      if (!isInputLikeTask(task)) continue
      const fp = inputFingerprint(task)
      const warnKey = `repeat-input:${lastInputKey}->${ei}:${ti}:${fp}`
      if (lastInputFp && fp && fp === lastInputFp) {
        if (!seenWarnKeys.has(warnKey)) {
          seenWarnKeys.add(warnKey)
          warns.push({ key: warnKey, message: REPEAT_INPUT_WARN })
        }
      }
      lastInputFp = fp
      lastInputKey = `${ei}:${ti}`
    }
  }
  return warns
}

export function resumeTask(baseTask, userText) {
  const tip = String(userText || '').trim()
  const base = String(baseTask || '完成页面上的操作')
  const antiRepeat =
    '不要重复已完成的步骤。验证码错误时先刷新验证码图再重新读图填写，禁止用同一串字符反复填验证码框；填完后必须点击登录或提交。'
  if (!tip || tip === '继续') {
    return (
      `${base}\n\n【人工协助】用户可能已在当前浏览器页面上手动完成部分操作（如输入验证码、点击登录）。` +
      `请基于当前页面状态继续完成目标。${antiRepeat}`
    )
  }
  return (
    `${base}\n\n【人工协助】用户提供了以下信息或指示，请使用它们继续完成目标：\n${tip}\n` +
    `用户也可能已在页面上手动操作过，请基于当前页面状态继续。${antiRepeat}`
  )
}
