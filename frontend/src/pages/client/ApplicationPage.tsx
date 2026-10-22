import { useEffect, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogContentText,
  DialogTitle, Paper, Stack, Typography,
} from '@mui/material';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link as RouterLink, Navigate, useParams } from 'react-router-dom';
import { api, ApiProtocolError } from '../../api/index.ts';
import { keys, productById, useApplication, useProducts, type ClientApplication, type Product } from '../../api/hooks.ts';
import { ApplicationTimeline } from '../../components/ApplicationTimeline.tsx';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { StatusChip, statusColor } from '../../components/StatusChip.tsx';
import { formatRub } from '../../lib/format.ts';
import { fill, productTexts, statusTexts, t } from '../../lib/texts.ts';

const SLOW_CHECKING_MS = 10_000;

/** Экран 5 (screen-map.md): таймлайн, карточка статуса, решение. Обновляется опросом до B09 (SSE). */
export function ApplicationPage() {
  const { id = '' } = useParams();
  const application = useApplication(id);
  const products = useProducts();

  if (application.isPending) return <CircularProgress aria-label={t.common.loading} />;
  if (application.error) return <ErrorAlert error={application.error} />;
  const app = application.data;
  if (app.status === 'DRAFT') return <Navigate to={`/client/applications/${id}/edit`} replace />;

  const product = productById(products.data, app.product_id);
  const productName = product ? productTexts(product.code)?.name ?? product.name : null;

  return (
    <Stack spacing={3}>
      <Box>
        <Button component={RouterLink} to="/client" size="small" sx={{ mb: 1 }}>← {t.applications.title}</Button>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', rowGap: 1 }}>
          <Typography component="h1" variant="h5" sx={{ fontWeight: 700 }}>
            {[app.company_name, productName, app.amount && formatRub(app.amount),
              app.term_months && `${app.term_months} ${t.common.months}`].filter(Boolean).join(' · ')}
          </Typography>
          <StatusChip status={app.status} />
        </Stack>
      </Box>

      <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 3 } }}>
        <ApplicationTimeline status={app.status} />
      </Paper>

      <StatusCard application={app} />
      {app.decision && <DecisionSummary application={app} products={products.data} />}
      <CancelAction application={app} />
    </Stack>
  );
}

function StatusCard({ application }: { application: ClientApplication }) {
  const texts = statusTexts(application.status);
  const slow = useSlowStatus(application.status);
  const color = statusColor(application.status);
  const accent = color === 'default' ? 'secondary.main' : `${color}.main`;
  return (
    <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3.5 }, borderLeft: '4px solid', borderLeftColor: accent }}>
      <Stack spacing={1} aria-live="polite">
        <Typography variant="h6" component="h2">{texts.title}</Typography>
        <Typography>{texts.description}</Typography>
        {texts.next && <Typography color="text.secondary">{texts.next}</Typography>}
        {slow && texts.slow && <Alert severity="info" sx={{ mt: 1 }}>{texts.slow}</Alert>}
      </Stack>
    </Paper>
  );
}

/** true, если заявка дольше 10 с в CHECKING: отдельного события ретрая в контракте нет. */
function useSlowStatus(status: ClientApplication['status']): boolean {
  const [since, setSince] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setSince(Date.now());
  }, [status]);
  useEffect(() => {
    if (status !== 'CHECKING') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [status]);
  return status === 'CHECKING' && now - since > SLOW_CHECKING_MS;
}

/**
 * Итог решения только для чтения. Действия — принять, отказаться, дополнить данные —
 * добавляет экран решения (#14), когда бэкенд реализует accept-offer, additional-info и sign.
 */
function DecisionSummary({ application, products }: { application: ClientApplication; products?: Product[] }) {
  const decision = application.decision;
  if (!decision) return null;
  const offer = 'offer' in decision ? decision.offer : null;
  const offeredProduct = offer ? productById(products, offer.terms.product_id) : undefined;

  return (
    <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3.5 } }}>
      <Stack spacing={2}>
        {offer && (
          <Stack spacing={1}>
            <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.offered}</Typography>
            <TermsRow label={t.decision.terms.product}
              value={offeredProduct ? productTexts(offeredProduct.code)?.name ?? offeredProduct.name : '—'} />
            <TermsRow label={t.decision.terms.amount} value={formatRub(offer.terms.amount)} />
            <TermsRow label={t.decision.terms.term} value={`${offer.terms.term_months} ${t.common.months}`} />
            <TermsRow
              label={t.decision.terms.rate}
              value={offer.terms.annual_rate_percent
                ? fill(t.decision.terms.rateValue, { rate: offer.terms.annual_rate_percent.replace('.', ',') })
                : t.decision.terms.rateTbd}
            />
          </Stack>
        )}

        {decision.outcome === 'COUNTER_OFFER' && decision.reasons.length > 0 && (
          <Stack spacing={0.5}>
            <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>
              {t.decision.counterOffer.whyTitle}
            </Typography>
            {decision.reasons.map((reason) => (
              <Typography key={reason.code}>
                {(t.counterOfferReasons as Record<string, string>)[reason.code] ?? reason.message}
              </Typography>
            ))}
          </Stack>
        )}

        {decision.outcome === 'REJECTED' && decision.reasons.map((reason) => {
          const known = (t.rejectionReasons as unknown as Record<string, { message: string; advice: string }>)[reason.code];
          return (
            <Stack key={reason.code} spacing={1}>
              <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.rejected.reasonTitle}</Typography>
              <Typography>{known?.message ?? reason.message}</Typography>
              {known?.advice && (
                <>
                  <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.rejected.adviceTitle}</Typography>
                  <Typography>{known.advice}</Typography>
                </>
              )}
            </Stack>
          );
        })}

        {decision.outcome === 'NEEDS_INFO' && (
          <Stack spacing={1}>
            <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.needsInfo.listTitle}</Typography>
            {decision.required_documents.map((doc) => (
              <Box key={doc.code}>
                <Typography sx={{ fontWeight: 500 }}>{doc.title}</Typography>
                <Typography variant="body2" color="text.secondary">{t.decision.needsInfo.why}: {doc.explanation}</Typography>
              </Box>
            ))}
          </Stack>
        )}

        {decision.outcome === 'REJECTED' && (
          <Button component={RouterLink} to="/client/new" variant="contained" sx={{ alignSelf: 'flex-start' }}>
            {t.decision.rejected.newApplication}
          </Button>
        )}
      </Stack>
    </Paper>
  );
}

function TermsRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography color="text.secondary" sx={{ minWidth: 160 }}>{label}</Typography>
      <Typography sx={{ fontWeight: 500 }}>{value}</Typography>
    </Stack>
  );
}

function CancelAction({ application }: { application: ClientApplication }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const cancel = useMutation({
    mutationFn: async () => {
      const { data } = await api.http.POST('/api/applications/{application_id}/cancel', {
        params: { path: { application_id: application.id } },
        body: { expected_revision: application.revision },
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(keys.application(data.id), data);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
      setOpen(false);
    },
  });

  if (!application.allowed_actions.includes('cancel')) return null;
  return (
    <Box>
      <Button color="error" variant="outlined" onClick={() => setOpen(true)}>{t.applications.cancel}</Button>
      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>{t.applications.cancelConfirmTitle}</DialogTitle>
        <DialogContent>
          <DialogContentText>{t.applications.cancelConfirmBody}</DialogContentText>
          {cancel.error && <Box sx={{ mt: 2 }}><ErrorAlert error={cancel.error} /></Box>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t.common.cancel}</Button>
          <Button color="error" variant="contained" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            {t.applications.cancelConfirm}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
