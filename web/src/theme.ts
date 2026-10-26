import { createTheme } from '@mui/material';

const fontFamily = '"Plus Jakarta Sans Variable", "Segoe UI", system-ui, -apple-system, sans-serif';

export const theme = createTheme({
  cssVariables: { colorSchemeSelector: 'class' },
  colorSchemes: {
    light: {
      palette: {
        primary: { main: '#0b7a83', light: '#3fa9b1', dark: '#075259', contrastText: '#ffffff' },
        secondary: { main: '#f08a4b', contrastText: '#ffffff' },
        background: { default: '#f5f8fa', paper: '#ffffff' },
        text: { primary: '#14232b', secondary: '#5b6b75' },
        divider: 'rgba(20, 35, 43, 0.08)',
      },
    },
    dark: {
      palette: {
        primary: { main: '#4fc3cb', light: '#7fd8de', dark: '#16858d', contrastText: '#06232a' },
        secondary: { main: '#ffa36c', contrastText: '#2a1306' },
        background: { default: '#0d1519', paper: '#141f25' },
        text: { primary: '#e6eef2', secondary: '#93a5ae' },
        divider: 'rgba(230, 238, 242, 0.08)',
      },
    },
  },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily,
    h4: { fontWeight: 700, letterSpacing: '-0.02em' },
    h5: { fontWeight: 700, letterSpacing: '-0.01em' },
    h6: { fontWeight: 600 },
    subtitle2: { fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { borderRadius: 10 } },
    },
    MuiPaper: {
      styleOverrides: { root: { backgroundImage: 'none' } },
    },
    MuiListItemButton: {
      styleOverrides: { root: { borderRadius: 10 } },
    },
    MuiTooltip: {
      defaultProps: { arrow: true },
    },
  },
});

export const brandGradient =
  'linear-gradient(135deg, var(--mui-palette-primary-main), var(--mui-palette-secondary-main))';
