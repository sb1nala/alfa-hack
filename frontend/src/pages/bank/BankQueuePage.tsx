import { useState } from 'react';
import {
  CircularProgress, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  ToggleButton, ToggleButtonGroup, Typography, useMediaQuery, useTheme,
} from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useUnderwriterQueue } from '../../api/bank.ts';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { StatusChip } from '../../components/StatusChip.tsx';
import { t } from '../../lib/texts.ts';

const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

/**
 * Экран 8 (screen-map.md): очередь аналитика. Контракт очереди сейчас отдаёт компанию,
 * статус и время; ИНН, продукт, сумма и класс риска видны в карточке заявки.
 */
export function BankQueuePage() {
  const [filter, setFilter] = useState<'MANUAL_REVIEW' | 'all'>('MANUAL_REVIEW');
  const queue = useUnderwriterQueue(filter);
  const navigate = useNavigate();
  // On a phone the date moves under the company name: three columns do not fit 375 px.
  const narrow = useMediaQuery(useTheme().breakpoints.down('sm'));

  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap' }}>
        <Typography component="h1" variant="h4" sx={{ fontWeight: 700 }}>
          {filter === 'all' ? 'Все заявки' : t.bank.queue.title}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={filter}
          onChange={(_, value) => value && setFilter(value)}
          aria-label="Фильтр очереди"
        >
          <ToggleButton value="MANUAL_REVIEW">Ручная проверка</ToggleButton>
          <ToggleButton value="all">Все заявки</ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      {queue.isPending && <CircularProgress aria-label={t.common.loading} />}
      {queue.error && <ErrorAlert error={queue.error} />}

      {queue.data && queue.data.items.length === 0 && (
        <Paper variant="outlined" sx={{ p: 5, textAlign: 'center' }}>
          <Typography>{t.bank.queue.empty}</Typography>
        </Paper>
      )}

      {queue.data && queue.data.items.length > 0 && (
        <TableContainer component={Paper} variant="outlined">
          <Table aria-label={t.bank.queue.title} size={narrow ? 'small' : 'medium'}>
            <TableHead>
              <TableRow>
                <TableCell>{t.bank.queue.columns.company}</TableCell>
                <TableCell>{t.bank.queue.columns.status}</TableCell>
                {!narrow && <TableCell>{t.bank.queue.columns.receivedAt}</TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {queue.data.items.map((item) => (
                <TableRow
                  key={item.id}
                  hover
                  tabIndex={0}
                  onClick={() => navigate(`/bank/applications/${item.id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/bank/applications/${item.id}`); }}
                  sx={{ cursor: 'pointer' }}
                >
                  <TableCell sx={{ fontWeight: 500 }}>
                    {item.company_name ?? '—'}
                    {narrow && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 400 }}>
                        {t.bank.queue.columns.receivedAt}: {dateTime(item.updated_at)}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell><StatusChip status={item.status} bank /></TableCell>
                  {!narrow && <TableCell>{dateTime(item.updated_at)}</TableCell>}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Stack>
  );
}
