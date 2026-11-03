import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { AUTH_EXPIRED_EVENT, apiJson } from '../api/client'

/** 与登录页「记住密码」约定一致，明文仅存本机，仅供个人可信设备。 */
export const REMEMBER_LOGIN_KEY = 'raina_remember_login'
export const SAVED_USERNAME_KEY = 'raina_saved_username'
export const SAVED_PASSWORD_KEY = 'raina_saved_password'

export type LoginOptions = {
  captchaId: string
  captchaCode: string
  rememberPassword: boolean
}

export type RegisterOptions = {
  captchaId: string
  captchaCode: string
  fullName?: string | undefined
}

type AuthContextValue = {
  token: string | null
  login: (
    username: string,
    password: string,
    opts: LoginOptions,
  ) => Promise<void>
  register: (username: string, password: string, opts: RegisterOptions) => Promise<string>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem('access_token'),
  )

  useEffect(() => {
    const onExpired = () => setToken(null)
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  const login = useCallback(
    async (username: string, password: string, opts: LoginOptions) => {
      const body: Record<string, string> = {
        username,
        password,
        captcha_id: opts.captchaId,
        captcha_code: opts.captchaCode,
      }
      const data = await apiJson<{ access_token: string; refresh_token: string }>(
        '/api/v1/auth/login',
        {
          method: 'POST',
          body: JSON.stringify(body),
        },
      )
      localStorage.setItem('access_token', data.access_token)
      localStorage.setItem('refresh_token', data.refresh_token)
      setToken(data.access_token)

      if (opts.rememberPassword) {
        localStorage.setItem(REMEMBER_LOGIN_KEY, '1')
        localStorage.setItem(SAVED_USERNAME_KEY, username)
        localStorage.setItem(SAVED_PASSWORD_KEY, password)
      } else {
        localStorage.removeItem(REMEMBER_LOGIN_KEY)
        localStorage.removeItem(SAVED_USERNAME_KEY)
        localStorage.removeItem(SAVED_PASSWORD_KEY)
      }
    },
    [],
  )

  const register = useCallback(
    async (username: string, password: string, opts: RegisterOptions) => {
      const body: Record<string, string> = {
        username,
        password,
      }
      if (opts.fullName) {
        body.full_name = opts.fullName
      }
      if (opts.captchaId) {
        body.captcha_id = opts.captchaId
        body.captcha_code = opts.captchaCode
      }
      const data = await apiJson<{ message: string }>('/api/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      return data.message
    },
    [],
  )

  const logout = useCallback(() => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    setToken(null)
  }, [])

  const value = useMemo(
    () => ({
      token,
      login,
      register,
      logout,
    }),
    [token, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}
