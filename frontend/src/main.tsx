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
    colorInfo: colorPrimary,
    colorLink: colorPrimary,
    colorTextBase: colorText,
    colorBgBase: colorBgSurface,
    colorBgLayout: colorBgPage,
    colorBorder,
    colorBorderSecondary: colorBorderSoft,
    borderRadius: radiusPx,
    controlHeight,
    fontFamily,
    boxShadow: 'none',
    boxShadowSecondary: 'none',
    boxShadowTertiary: 'none',
  },
  components: {
    Button: {
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
      controlHeight,
      paddingInline: 12,
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
      boxShadow: 'none',
      boxShadowTertiary: 'none',
    },
    Modal: {
      boxShadow: 'none',
      titleFontSize: 16,
    },
    Drawer: {
      boxShadow: 'none',
    },
    Dropdown: {
      boxShadow: 'none',
    },
    Popover: {
      boxShadow: 'none',
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
