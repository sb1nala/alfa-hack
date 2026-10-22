import { Box, Button, Card, CardActionArea, CircularProgress, Paper, Stack, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { productById, useApplications, useProducts } from '../../api/hooks.ts';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { StatusChip } from '../../components/StatusChip.tsx';
import { fill, productTexts, t } from '../../lib/texts.ts';
import { formatDate, formatRub } from '../../lib/format.ts';

/** Экран 2 (screen-map.md): список заявок клиента. */
export function ApplicationsPage() {
  const applications = useApplications();
  const products = useProducts();

  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap' }}>
        <Typography component="h1" variant="h4" sx={{ fontWeight: 700 }}>{t.applications.title}</Typography>
        <Button component={RouterLink} to="/client/new" variant="contained" size="large">
          {t.applications.newApplication}
        </Button>
      </Stack>

      {applications.isPending && <CircularProgress aria-label={t.common.loading} />}
      {applications.error && <ErrorAlert error={applications.error} />}

      {applications.data && applications.data.items.length === 0 && (
        <Paper variant="outlined" sx={{ p: 5, textAlign: 'center' }}>
          <Stack spacing={2} sx={{ alignItems: 'center' }}>
            <Typography variant="h6" component="p">{t.applications.empty}</Typography>
            <Button component={RouterLink} to="/client/new" variant="contained" color="secondary">
              {t.applications.emptyAction}
            </Button>
          </Stack>
        </Paper>
      )}

      <Stack spacing={1.5}>
        {applications.data?.items.map((item) => {
          const product = productById(products.data, item.product_id);
          const productName = product ? productTexts(product.code)?.name ?? product.name : null;
          const to = item.status === 'DRAFT'
            ? `/client/applications/${item.id}/edit`
            : `/client/applications/${item.id}`;
          return (
            <Card key={item.id} variant="outlined">
              <CardActionArea component={RouterLink} to={to} sx={{ p: 2.5 }}>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
                  <Box>
                    <Typography sx={{ fontWeight: 600 }}>
                      {item.company_name ?? 'Компания не указана'}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {[productName, item.amount && formatRub(item.amount),
                        item.term_months && `${item.term_months} ${t.common.months}`]
                        .filter(Boolean).join(' · ') || 'Черновик без условий'}
                    </Typography>
                  </Box>
                  <Stack spacing={0.5} sx={{ alignItems: { sm: 'flex-end' } }}>
                    <StatusChip status={item.status} />
                    <Typography variant="caption" color="text.secondary">
                      {item.status === 'DRAFT'
                        ? `Создана ${formatDate(item.created_at)}`
                        : fill(t.applications.submittedAt, { date: formatDate(item.updated_at) })}
                    </Typography>
                  </Stack>
                </Stack>
              </CardActionArea>
            </Card>
          );
        })}
      </Stack>
    </Stack>
  );
}
