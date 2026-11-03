import { ReloadOutlined } from '@ant-design/icons'
import { type FormEvent, useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError, apiJson } from '../api/client'

type CaptchaResponse = {
  captcha_id: string
  image_base64: string
}

type RegisterOptions = {
  allowed: boolean
  require_captcha: boolean
}

function AuthCard({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <div className="card narrow">{children}</div>
    </div>
  )
}

export default function Register() {
  const { register, token } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [fullName, setFullName] = useState('')
  const [captchaId, setCaptchaId] = useState('')
  const [captchaCode, setCaptchaCode] = useState('')
  const [captchaSrc, setCaptchaSrc] = useState<string | null>(null)
  const [requireCaptcha, setRequireCaptcha] = useState(true)
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const loadCaptcha = useCallback(async () => {
    try {
      const data = await apiJson<CaptchaResponse>('/api/v1/auth/captcha')
      setCaptchaId(data.captcha_id)
      setCaptchaSrc(`data:image/png;base64,${data.image_base64}`)
      setCaptchaCode('')
    } catch {
      setCaptchaSrc(null)
      setCaptchaId('')
    }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const opts = await apiJson<RegisterOptions>('/api/v1/auth/register-options')
        setAllowed(opts.allowed)
        setRequireCaptcha(opts.require_captcha)
        if (opts.require_captcha) {
          await loadCaptcha()
        }
      } catch {
        setAllowed(false)
      }
    })()
  }, [loadCaptcha])

  if (token) {
    return <Navigate to="/projects" replace />
  }

  if (allowed === false) {
    return (
      <AuthCard>
        <p className="auth-card-brand">RainaAI测试平台</p>
        <h1>注册</h1>
        <p className="muted">当前未开放自助注册，请联系管理员创建账号。</p>
        <p style={{ marginTop: 16 }}>
          <Link to="/login">返回登录</Link>
        </p>
      </AuthCard>
    )
  }

  if (allowed === null) {
    return (
      <AuthCard>
        <p className="muted">加载中…</p>
      </AuthCard>
    )
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setErr(null)
    if (password !== password2) {
      setErr('两次输入的密码不一致')
      return
    }
    if (requireCaptcha && !captchaId) {
      setErr('请等待验证码加载完成')
      return
    }
    try {
      const msg = await register(username, password, {
        fullName: fullName.trim() || undefined,
        captchaId: requireCaptcha ? captchaId : '',
        captchaCode: requireCaptcha ? captchaCode : '',
      })
      setSuccessMsg(msg)
    } catch (ex) {
      if (requireCaptcha) void loadCaptcha()
      if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
        const m = ex.body as { message?: string }
        setErr(m.message ?? '注册失败')
      } else {
        setErr('注册失败')
      }
    }
  }

  if (successMsg) {
    return (
      <AuthCard>
        <p className="auth-card-brand">RainaAI测试平台</p>
        <h1>注册</h1>
        <p style={{ marginTop: 8 }}>{successMsg}</p>
        <p className="muted" style={{ marginTop: 16, marginBottom: 0 }}>
          <Link to="/login">去登录</Link>
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard>
      <p className="auth-card-brand">RainaAI测试平台</p>
      <h1>注册</h1>
      <form onSubmit={onSubmit} className="form">
        <label>
          账号
          <span className="field-hint">3–32 位，字母开头，仅字母/数字/下划线</span>
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
          <span className="field-hint">至少 8 位</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
          />
        </label>
        <label>
          确认密码
          <input
            type="password"
            value={password2}
            onChange={(e) => setPassword2(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
          />
        </label>
        <label>
          姓名（可选）
          <input
            type="text"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            autoComplete="name"
          />
        </label>
        {requireCaptcha && (
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
        )}
        {err && <p className="error">{err}</p>}
        <button type="submit" className="btn primary">
          注册
        </button>
      </form>
      <p className="muted" style={{ marginTop: 16, marginBottom: 0 }}>
        已有账号？<Link to="/login">去登录</Link>
      </p>
    </AuthCard>
  )
}
