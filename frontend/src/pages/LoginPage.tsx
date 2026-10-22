import { useState, type FormEvent } from 'react';
import { Alert, Box, Button, Paper, Stack, TextField, Typography } from '@mui/material';
import { useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/index.ts';
import { keys, useMe } from '../api/hooks.ts';
import { homeOf } from '../app/RequireRole.tsx';
import { errorMessage } from '../components/ErrorAlert.tsx';
import { t } from '../lib/texts.ts';

/** Экран 1 (screen-map.md): общий вход, после него — кабинет своей роли. */
export function LoginPage() {
  const me = useMe();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (me.data) return <Navigate to={homeOf(me.data.role)} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.auth.login({ login: login.trim(), password });
      const user = await api.auth.me();
      queryClient.setQueryData(keys.me, user);
      const from = (location.state as { from?: string } | null)?.from;
      const home = homeOf(user.role);
      navigate(from?.startsWith(home) ? from : home, { replace: true });
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? t.login.errorInvalid : errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center' }}>
      <Paper variant="outlined" component="form" onSubmit={submit} sx={{ p: { xs: 3, md: 5 }, width: '100%', maxWidth: 420 }}>
        <Stack spacing={3}>
          <Typography component="h1" variant="h5" sx={{ fontWeight: 700 }}>{t.login.title}</Typography>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label={t.login.loginLabel}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
          <TextField
            label={t.login.passwordLabel}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          <Button type="submit" variant="contained" size="large" disabled={busy}>
            {t.login.submit}
          </Button>
          <Typography variant="body2" color="text.secondary">{t.login.demoHint}</Typography>
        </Stack>
      </Paper>
    </Box>
  );
}
