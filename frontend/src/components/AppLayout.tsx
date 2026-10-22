import { AppBar, Box, Button, Container, Stack, Toolbar, Typography } from '@mui/material';
import { useQueryClient } from '@tanstack/react-query';
import { Link as RouterLink, Outlet, useNavigate } from 'react-router-dom';
import { api } from '../api/index.ts';
import { useMe } from '../api/hooks.ts';
import { closeApplicationStreams } from '../api/applicationLive.ts';
import { t } from '../lib/texts.ts';

export function AppLayout() {
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const user = me.data;

  async function logout() {
    closeApplicationStreams();
    try {
      await api.auth.logout();
    } finally {
      queryClient.clear();
      navigate('/login', { replace: true });
    }
  }

  return (
    <Box sx={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column' }}>
      <Button
        href="#main-content"
        sx={{ position: 'absolute', top: -100, left: 16, zIndex: 1300,
          '&:focus': { top: 8, bgcolor: 'background.paper' } }}
      >
        Перейти к содержимому
      </Button>
      <AppBar position="static" color="inherit" elevation={0} sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Container maxWidth="lg">
          <Toolbar disableGutters sx={{ py: 2, gap: 2, flexWrap: 'wrap' }}>
            <Typography
              component={RouterLink}
              to="/"
              variant="h6"
              sx={{ color: 'primary.main', fontWeight: 700, mr: 'auto', textDecoration: 'none' }}
            >
              {user?.role === 'underwriter' ? t.common.bankCabinetName : t.common.appName}
            </Typography>
            {user && (
              <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
                <Typography variant="body2" color="text.secondary">{user.display_name}</Typography>
                <Button variant="outlined" size="small" onClick={logout}>{t.common.logout}</Button>
              </Stack>
            )}
          </Toolbar>
        </Container>
      </AppBar>
      <Container component="main" id="main-content" tabIndex={-1} maxWidth="lg" sx={{ flex: 1, py: { xs: 4, md: 6 } }}>
        <Outlet />
      </Container>
      <Container component="footer" maxWidth="lg" sx={{ pb: 3 }}>
        <Typography variant="body2" color="text.secondary">
          Alfa Hack 2026 · Демонстрационный прототип · Синтетические данные
        </Typography>
      </Container>
    </Box>
  );
}
