import { useState } from 'react';
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Grid,
  Paper, Stack, Typography,
} from '@mui/material';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { api, ApiProtocolError } from '../../api/index.ts';
import { keys, productById, type ClientApplication, type Product } from '../../api/hooks.ts';
import { ErrorAlert } from '../ErrorAlert.tsx';
import { NeedsInfoForm } from './NeedsInfoForm.tsx';
import { currentOffer } from '../../lib/clientActions.ts';
import { formatRub, monthlyPayment } from '../../lib/format.ts';
import { fill, productTexts, t } from '../../lib/texts.ts';

type Props = { application: ClientApplication; products?: Product[] };

/**
 * Экран 7 (screen-map.md): блок решения внутри страницы заявки. Действия — по allowed_actions:
 * принять предложение → подписание, отказаться → закрыть заявку, дополнить данные.
 */
export function DecisionBlock({ application, products }: Props) {
  const decision = application.decision;
  const actions = application.allowed_actions;

  if (application.status === 'OFFER_ACCEPTED' && actions.includes('sign')) {
    return (
      <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3.5 } }}>
        <Stack spacing={2} sx={{ alignItems: 'flex-start' }}>
          <OfferTerms application={application} products={products} title={t.decision.offered} />
          <Button component={RouterLink} to={`/client/applications/${application.id}/sign`} variant="contained" size="large">
            {t.signing.title}
          </Button>
        </Stack>
      </Paper>
    );
  }
  if (!decision) return null;

  return (
    <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3.5 } }}>
      <Stack spacing={2.5}>
        {decision.outcome === 'COUNTER_OFFER' && (
          <Grid container spacing={3}>
            <Grid size={{ xs: 12, sm: 6 }}>
              <RequestedTerms application={application} products={products} />
            </Grid>
            <Grid size={{ xs: 12, sm: 6 }}>
              <OfferTerms application={application} products={products} title={t.decision.offered} />
            </Grid>
          </Grid>
        )}
        {decision.outcome === 'APPROVED' && (
          <OfferTerms application={application} products={products} title={t.decision.offered} />
        )}

        {decision.outcome === 'COUNTER_OFFER' && decision.reasons.length > 0 && (
          <Stack spacing={0.5}>
            <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>
              {t.decision.counterOffer.whyTitle}
            </Typography>
            {decision.reasons.map((reason) => (
              <Typography key={reason.code}>
                {(t.counterOfferReasons as unknown as Record<string, string>)[reason.code] ?? reason.message}
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

        {decision.outcome === 'NEEDS_INFO' && (actions.includes('provide_info')
          ? <NeedsInfoForm application={application} documents={decision.required_documents} />
          : <RequiredList documents={decision.required_documents} />)}

        {(decision.outcome === 'APPROVED' || decision.outcome === 'COUNTER_OFFER') && (
          <OfferActions application={application} counter={decision.outcome === 'COUNTER_OFFER'} />
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

function OfferActions({ application, counter }: { application: ClientApplication; counter: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const offer = currentOffer(application);
  const texts = counter ? t.decision.counterOffer : t.decision.approved;

  const accept = useMutation({
    mutationFn: async () => {
      if (!offer) throw new ApiProtocolError();
      const { data } = await api.http.POST('/api/applications/{application_id}/accept-offer', {
        params: { path: { application_id: application.id } },
        body: { expected_revision: application.revision, offer_id: offer.id },
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(keys.application(data.id), data);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
      navigate(`/client/applications/${data.id}/sign`);
    },
  });

  const decline = useMutation({
    mutationFn: async () => {
      const { data } = await api.http.POST('/api/applications/{application_id}/cancel', {
        params: { path: { application_id: application.id } },
        body: { expected_revision: application.revision, reason: 'Клиент отказался от предложения банка' },
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(keys.application(data.id), data);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
      setConfirm(false);
    },
  });

  const canAccept = application.allowed_actions.includes('accept_offer') && offer;
  const canDecline = application.allowed_actions.includes('cancel');
  if (!canAccept && !canDecline) return null;

  return (
    <Box>
      {accept.error && <Box sx={{ mb: 2 }}><ErrorAlert error={accept.error} /></Box>}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        {canAccept && (
          <Button variant="contained" size="large" onClick={() => accept.mutate()} disabled={accept.isPending}>
            {texts.accept}
          </Button>
        )}
        {canDecline && (
          <Button variant="outlined" size="large" onClick={() => setConfirm(true)}>{texts.decline}</Button>
        )}
      </Stack>
      <Dialog open={confirm} onClose={() => setConfirm(false)}>
        <DialogTitle>{t.decision.declineConfirm.title}</DialogTitle>
        <DialogContent>
          <DialogContentText>{t.decision.declineConfirm.body}</DialogContentText>
          {decline.error && <Box sx={{ mt: 2 }}><ErrorAlert error={decline.error} /></Box>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(false)}>{t.decision.declineConfirm.cancel}</Button>
          <Button color="error" variant="contained" onClick={() => decline.mutate()} disabled={decline.isPending}>
            {t.decision.declineConfirm.confirm}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export function OfferTerms({ application, products, title }: Props & { title: string }) {
  const offer = currentOffer(application);
  if (!offer) return null;
  const product = productById(products, offer.terms.product_id);
  const rate = offer.terms.annual_rate_percent;
  const payment = rate ? monthlyPayment(Number(offer.terms.amount), offer.terms.term_months, Number(rate)) : 0;
  return (
    <Stack spacing={1}>
      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{title}</Typography>
      <TermsRow label={t.decision.terms.product} value={product ? productTexts(product.code)?.name ?? product.name : '—'} />
      <TermsRow label={t.decision.terms.amount} value={formatRub(offer.terms.amount)} />
      <TermsRow label={t.decision.terms.term} value={`${offer.terms.term_months} ${t.common.months}`} />
      <TermsRow
        label={t.decision.terms.rate}
        value={rate ? fill(t.decision.terms.rateValue, { rate: rate.replace('.', ',') }) : t.decision.terms.rateTbd}
      />
      {payment > 0 && <TermsRow label={t.decision.terms.monthlyPayment} value={formatRub(Math.round(payment))} />}
    </Stack>
  );
}

function RequestedTerms({ application, products }: Props) {
  const product = productById(products, application.product_id);
  return (
    <Stack spacing={1}>
      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.requested}</Typography>
      <TermsRow label={t.decision.terms.product} value={product ? productTexts(product.code)?.name ?? product.name : '—'} />
      <TermsRow label={t.decision.terms.amount} value={formatRub(application.amount)} />
      <TermsRow label={t.decision.terms.term} value={application.term_months ? `${application.term_months} ${t.common.months}` : '—'} />
    </Stack>
  );
}

function RequiredList({ documents }: { documents: { code: string; title: string; explanation: string }[] }) {
  return (
    <Stack spacing={1}>
      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.needsInfo.listTitle}</Typography>
      {documents.map((doc) => (
        <Box key={doc.code}>
          <Typography sx={{ fontWeight: 500 }}>{doc.title}</Typography>
          <Typography variant="body2" color="text.secondary">{t.decision.needsInfo.why}: {doc.explanation}</Typography>
        </Box>
      ))}
      <Alert severity="info" variant="outlined">Данные отправлены — банк их проверяет.</Alert>
    </Stack>
  );
}

function TermsRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography color="text.secondary" sx={{ minWidth: 150 }}>{label}</Typography>
      <Typography sx={{ fontWeight: 500 }}>{value}</Typography>
    </Stack>
  );
}
