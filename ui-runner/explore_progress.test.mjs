import assert from 'node:assert/strict'
import test from 'node:test'
import {
  REPEAT_INPUT_WARN,
  detectRepeatedInputWarns,
  progressKeysFromDump,
  resumeTask,
} from './explore_progress.mjs'

test('progressKeysFromDump skips yamlFlow when Action Space Input is present', () => {
  const dump = {
    executions: [
      {
        tasks: [
          {
            type: 'Action Space',
            subType: 'Input',
            status: 'finished',
            param: { locate: '验证码输入框', value: '8n7' },
            output: {
              yamlFlow: [{ aiInput: '8n7', locate: '验证码输入框' }],
            },
          },
        ],
      },
    ],
  }
  const keys = progressKeysFromDump(dump)
  const messages = keys.map((k) => k.message)
  assert.ok(messages.some((m) => m.includes('任务：') && m.includes('Input')))
  assert.ok(!messages.some((m) => m.startsWith('填写')))
})

test('progressKeysFromDump still emits flow for Planning-only tasks', () => {
  const dump = {
    executions: [
      {
        tasks: [
          {
            type: 'Planning',
            status: 'finished',
            output: {
              yamlFlow: [{ aiInput: '8n7', locate: '验证码输入框' }],
            },
          },
        ],
      },
    ],
  }
  const keys = progressKeysFromDump(dump)
  const messages = keys.map((k) => k.message)
  assert.ok(messages.some((m) => m.includes('填写「验证码输入框」')))
})

test('detectRepeatedInputWarns on two identical Inputs without Click', () => {
  const dump = {
    executions: [
      {
        tasks: [
          {
            type: 'Action Space',
            subType: 'Input',
            status: 'finished',
            param: { locate: '验证码输入框', value: '8n7' },
          },
          {
            type: 'Action Space',
            subType: 'Input',
            status: 'finished',
            param: { locate: '验证码输入框', value: '8n7' },
          },
        ],
      },
    ],
  }
  const warns = detectRepeatedInputWarns(dump)
  assert.equal(warns.length, 1)
  assert.equal(warns[0].message, REPEAT_INPUT_WARN)
})

test('detectRepeatedInputWarns resets after Click', () => {
  const dump = {
    executions: [
      {
        tasks: [
          {
            type: 'Action Space',
            subType: 'Input',
            status: 'finished',
            param: { locate: '验证码输入框', value: '8n7' },
          },
          {
            type: 'Action Space',
            subType: 'Tap',
            status: 'finished',
            param: { locate: '登录' },
          },
          {
            type: 'Action Space',
            subType: 'Input',
            status: 'finished',
            param: { locate: '验证码输入框', value: '8n7' },
          },
        ],
      },
    ],
  }
  assert.equal(detectRepeatedInputWarns(dump).length, 0)
})

test('resumeTask discourages repeating same captcha', () => {
  const text = resumeTask('完成登录', '继续')
  assert.match(text, /不要重复已完成的步骤/)
  assert.match(text, /刷新验证码/)
  assert.match(text, /禁止用同一串/)
})
