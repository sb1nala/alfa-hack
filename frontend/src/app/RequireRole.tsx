import type { ReactNode } from 'react';
import { Box, CircularProgress } from '@mui/material';
import { Navigate, useLocation } from 'react-router-dom';
import { ApiError } from '../api/index.ts';
import { useMe } from '../api/hooks.ts';
import { ErrorAlert } from '../components/ErrorAlert.tsx';

type Role = 'client' | 'underwriter';

export function homeOf(role: Role): string {
  return role === 'underwriter' ? '/bank' : '/client';
}

/** Пускает только пользователя нужной роли; без сессии — на вход, чужая роль — в свой кабинет. */
export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const me = useMe();
  const location = useLocation();

  if (me.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress aria-label="Загружаем" />
      </Box>
    );
  }
  if (me.error) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate to="/login" replace state={{ from: location.pathname }} />;
    }
    return <ErrorAlert error={me.error} />;
  }
  if (me.data.role !== role) return <Navigate to={homeOf(me.data.role)} replace />;
  return <>{children}</>;
}
