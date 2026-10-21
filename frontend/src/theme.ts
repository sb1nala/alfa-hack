import { createTheme } from '@mui/material/styles';

// Источник: docs/product/design-tokens.md (утверждено продуктом 21.10).
export const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: '#800020', dark: '#670015', light: '#A02F3B', contrastText: '#FFFFFF' },
    secondary: { main: '#0ABAB5', dark: '#00716E', light: '#4CCAC5', contrastText: '#1C1A1E' },
    success: { main: '#00716E', dark: '#004240', light: '#DBFEFC', contrastText: '#FFFFFF' },
    warning: { main: '#F2A900', dark: '#733500', light: '#FFF3D3', contrastText: '#1C1A1E' },
    info: { main: '#2654BF', dark: '#032483', light: '#E6F7FF', contrastText: '#FFFFFF' },
    error: { main: '#B01000', dark: '#730000', light: '#FFE9E0', contrastText: '#FFFFFF' },
    text: { primary: '#211E23', secondary: '#5F5C63', disabled: '#97939B' },
    divider: '#D3CFD7',
    background: { default: '#F7F3FA', paper: '#FFFFFF' },
    grey: {
      50: '#F7F3FA', 100: '#EAE6ED', 200: '#D3CFD7', 300: '#B6B2BA', 400: '#97939B',
      500: '#79757D', 600: '#5F5C63', 700: '#49464D', 800: '#343138', 900: '#211E23',
    },
  },
  shape: { borderRadius: 8 },
  typography: { button: { textTransform: 'none', fontWeight: 500 } },
  components: {
    MuiLink: { defaultProps: { color: 'secondary.dark' } },
    MuiButton: { defaultProps: { disableElevation: true } },
  },
});
