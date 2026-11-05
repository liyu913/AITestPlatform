import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import './index.css'
import App from './App.tsx'
import { ApiError } from './api/client'
import { AuthProvider } from './auth/AuthContext'
import {
  colorBgActive,
  colorBgHover,
  colorBgMuted,
  colorBgPage,
  colorBgSurface,
  colorBorder,
  colorBorderSoft,
  colorPrimary,
  colorPrimaryActive,
  colorPrimaryHover,
  colorText,
  colorTextSecondary,
  controlHeight,
  fontFamily,
  radiusPx,
} from './theme/tokens'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status === 401) return false
        return failureCount < 1
      },
      refetchOnWindowFocus: false,
    },
  },
})

const flatLightTheme = {
  token: {
    colorPrimary,
    colorPrimaryHover,
    colorPrimaryActive,
    colorInfo: colorPrimary,
    colorLink: colorPrimary,
    colorLinkHover: colorPrimaryHover,
    colorTextBase: colorText,
    colorBgBase: colorBgSurface,
    colorBgLayout: colorBgPage,
    colorBorder,
    colorBorderSecondary: colorBorderSoft,
    borderRadius: radiusPx,
    controlHeight,
    fontFamily,
    boxShadow: '0 6px 18px rgba(26, 29, 38, 0.06)',
    boxShadowSecondary: '0 10px 24px rgba(26, 29, 38, 0.08)',
    boxShadowTertiary: '0 6px 8px rgba(26, 29, 38, 0.024)',
  },
  components: {
    Button: {
      primaryShadow: '0 2px 6px rgba(254, 103, 2, 0.16)',
      defaultShadow: 'none',
      dangerShadow: 'none',
      controlHeight,
      paddingInline: 14,
    },
    Input: {
      controlHeight,
      paddingInline: 11,
    },
    Select: {
      controlHeight,
      optionSelectedBg: colorBgActive,
    },
    Card: {
      boxShadowTertiary: '0 6px 8px rgba(26, 29, 38, 0.024)',
    },
    Modal: {
      titleFontSize: 16,
    },
    Drawer: {},
    Dropdown: {
      boxShadowSecondary: '0 10px 24px rgba(26, 29, 38, 0.08)',
    },
    Popover: {
      boxShadowSecondary: '0 10px 24px rgba(26, 29, 38, 0.08)',
    },
    Table: {
      headerBg: colorBgMuted,
      rowHoverBg: colorBgHover,
      borderColor: colorBorder,
      cellPaddingBlock: 10,
      cellPaddingInline: 12,
    },
    Menu: {
      itemSelectedBg: colorBgActive,
      itemSelectedColor: colorPrimary,
    },
    Form: {
      itemMarginBottom: 16,
      verticalLabelPadding: '0 0 6px',
      labelColor: colorTextSecondary,
      labelFontSize: 13,
    },
    Segmented: {
      itemSelectedBg: colorBgSurface,
      itemSelectedColor: colorPrimary,
      trackBg: colorBgMuted,
    },
  },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={flatLightTheme}>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  </StrictMode>,
)
