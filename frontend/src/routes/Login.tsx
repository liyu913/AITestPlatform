import { ReloadOutlined } from '@ant-design/icons'
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import {
  REMEMBER_LOGIN_KEY,
  SAVED_USERNAME_KEY,
  SAVED_PASSWORD_KEY,
  useAuth,
} from '../auth/AuthContext'
import { ApiError, apiJson } from '../api/client'

type CaptchaResponse = {
  captcha_id: string
  image_base64: string
}

export default function Login() {
  const { login, token } = useAuth()
  const nav = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [captchaId, setCaptchaId] = useState('')
  const [captchaCode, setCaptchaCode] = useState('')
  const [captchaSrc, setCaptchaSrc] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const loadCaptcha = useCallback(async () => {
    setErr(null)
    try {
      const data = await apiJson<CaptchaResponse>('/api/v1/auth/captcha')
      setCaptchaId(data.captcha_id)
      setCaptchaSrc(`data:image/png;base64,${data.image_base64}`)
      setCaptchaCode('')
    } catch (ex) {
      setCaptchaSrc(null)
      setCaptchaId('')
      if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
        const m = ex.body as { message?: string }
        setErr(m.message ?? '加载验证码失败')
      } else {
        setErr('加载验证码失败')
      }
    }
  }, [])

  useEffect(() => {
    try {
      if (localStorage.getItem(REMEMBER_LOGIN_KEY) === '1') {
        const u = localStorage.getItem(SAVED_USERNAME_KEY)
        const pw = localStorage.getItem(SAVED_PASSWORD_KEY)
        if (u) setUsername(u)
        if (pw) setPassword(pw)
        setRemember(true)
      }
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    void loadCaptcha()
  }, [loadCaptcha])

  if (token) {
    return <Navigate to="/projects" replace />
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setErr(null)
    if (!captchaId) {
      setErr('请等待验证码加载完成')
      return
    }
    try {
      await login(username, password, {
        captchaId,
        captchaCode,
        rememberPassword: remember,
      })
      nav('/projects')
    } catch (ex) {
      void loadCaptcha()
      if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
        const m = ex.body as { message?: string }
        setErr(m.message ?? '登录失败')
      } else {
        setErr('登录失败')
      }
    }
  }

  return (
    <div className="auth-shell">
      <div className="card narrow">
        <p className="auth-card-brand">RainaAI测试平台</p>
        <h1>登录</h1>
        <form onSubmit={onSubmit} className="form">
          <label>
            账号
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              maxLength={32}
              pattern="[a-zA-Z][a-zA-Z0-9_]{2,31}"
              title="3–32 位，字母开头，仅字母/数字/下划线"
              autoComplete="username"
            />
          </label>
          <label>
            密码
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </label>
          <div className="captcha-block">
            <div className="captcha-inline">
              <label className="captcha-input-label">
                验证码
                <input
                  type="text"
                  value={captchaCode}
                  onChange={(e) => setCaptchaCode(e.target.value)}
                  required
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="请输入图中字符"
                />
              </label>
              <div className="captcha-media">
                {captchaSrc ? (
                  <img src={captchaSrc} alt="" />
                ) : (
                  <span className="captcha-placeholder">加载中…</span>
                )}
                <button
                  type="button"
                  className="btn secondary btn-captcha-refresh"
                  onClick={() => void loadCaptcha()}
                  aria-label="换一张验证码"
                  title="换一张"
                >
                  <ReloadOutlined />
                </button>
              </div>
            </div>
          </div>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            记住密码
          </label>
          {err && <p className="error">{err}</p>}
          <button type="submit" className="btn primary">
            登录
          </button>
        </form>
        <p className="muted" style={{ marginTop: 16, marginBottom: 0 }}>
          没有账号？<Link to="/register">去注册</Link>
        </p>
      </div>
    </div>
  )
}
