import { createContext } from 'react'

/* 主题名(ECharts 重建配色依赖它) */
export type ThemeName = 'light' | 'dark'
export const ThemeContext = createContext<ThemeName>('light')
