import { AppBar, Box, Button, Container, Stack, Toolbar, Typography } from '@mui/material';
import { NavLink, Outlet } from 'react-router-dom';

export function AppLayout() {
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
            <Typography component="span" variant="h6" sx={{ color: 'primary.main', fontWeight: 700, mr: 'auto' }}>
              Кредитный кабинет
            </Typography>
            <Stack component="nav" aria-label="Разделы кабинета" direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
              <Button component={NavLink} to="/client" sx={{ '&.active': { bgcolor: 'primary.main', color: 'primary.contrastText' } }}>
                Предпринимателю
              </Button>
              <Button component={NavLink} to="/bank" sx={{ '&.active': { bgcolor: 'primary.main', color: 'primary.contrastText' } }}>
                Сотруднику банка
              </Button>
            </Stack>
          </Toolbar>
        </Container>
      </AppBar>
      <Container component="main" id="main-content" tabIndex={-1} maxWidth="lg" sx={{ flex: 1, py: { xs: 4, md: 7 } }}>
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
