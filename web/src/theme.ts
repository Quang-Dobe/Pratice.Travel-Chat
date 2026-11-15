import { createTheme } from '@mui/material';

const fontFamily = '"Plus Jakarta Sans Variable", "Segoe UI", system-ui, -apple-system, sans-serif';

export const theme = createTheme({
  cssVariables: { colorSchemeSelector: 'class' },
  colorSchemes: {
    light: {
      palette: {
        primary: { main: '#0f7b78', light: '#45a8a4', dark: '#085654', contrastText: '#ffffff' },
        secondary: { main: '#f2874b', contrastText: '#ffffff' },
        background: { default: '#fbf6ef', paper: '#fffdf9' },
        text: { primary: '#2a2420', secondary: '#6d625a' },
        divider: 'rgba(66, 44, 28, 0.12)',
      },
    },
    dark: {
      palette: {
        primary: { main: '#4fc3cb', light: '#7fd8de', dark: '#16858d', contrastText: '#06232a' },
        secondary: { main: '#ffa36c', contrastText: '#2a1306' },
        background: { default: '#171311', paper: '#221c19' },
        text: { primary: '#f3ece6', secondary: '#b3a69c' },
        divider: 'rgba(243, 236, 230, 0.12)',
      },
    },
  },
  shape: { borderRadius: 16 },
  spacing: 9,
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
      styleOverrides: { root: { borderRadius: 999 } },
    },
    MuiPaper: {
      styleOverrides: { root: { backgroundImage: 'none' } },
    },
    MuiListItemButton: {
      styleOverrides: { root: { borderRadius: 14 } },
    },
    MuiChip: {
      styleOverrides: { root: { borderRadius: 999 } },
    },
    MuiTooltip: {
      defaultProps: { arrow: true },
    },
  },
});

export const brandGradient =
  'linear-gradient(135deg, var(--mui-palette-primary-main), var(--mui-palette-secondary-main))';
