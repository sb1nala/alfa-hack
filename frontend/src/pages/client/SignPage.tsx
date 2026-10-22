import { useState } from 'react';
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, FormControlLabel, Paper, Stack, Typography,
} from '@mui/material';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link as RouterLink, Navigate, useNavigate, useParams } from 'react-router-dom';
import { api, ApiProtocolError } from '../../api/index.ts';
import { keys, productById, useApplication, useProducts } from '../../api/hooks.ts';
import { OfferTerms } from '../../components/client/DecisionBlock.tsx';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { currentOffer } from '../../lib/clientActions.ts';
import { formatRub } from '../../lib/format.ts';
import { productTexts, t } from '../../lib/texts.ts';

/** Экран 6 (screen-map.md): подписание договора — симуляция, без реальной ЭП. */
export function SignPage() {
  const { id = '' } = useParams();
  const application = useApplication(id);
  const products = useProducts();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [agreed, setAgreed] = useState(false);
  const [contractOpen, setContractOpen] = useState(false);

  const sign = useMutation({
    mutationFn: async () => {
      const app = application.data;
      const offer = app && currentOffer(app);
      if (!app || !offer) throw new ApiProtocolError();
      const { data } = await api.http.POST('/api/applications/{application_id}/sign', {
        params: { path: { application_id: app.id } },
        body: { expected_revision: app.revision, offer_id: offer.id, consent: true },
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(keys.application(data.id), data);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
      navigate(`/client/applications/${data.id}`, { replace: true });
    },
  });

  if (application.isPending) return <CircularProgress aria-label={t.common.loading} />;
  if (application.error) return <ErrorAlert error={application.error} />;
  const app = application.data;
  if (!app.allowed_actions.includes('sign')) return <Navigate to={`/client/applications/${id}`} replace />;

  const offer = currentOffer(app);
  const product = productById(products.data, offer?.terms.product_id);
  const productName = product ? productTexts(product.code)?.name ?? product.name : '';

  return (
    <Stack spacing={3} sx={{ maxWidth: 720 }}>
      <Box>
        <Button component={RouterLink} to={`/client/applications/${id}`} size="small" sx={{ mb: 1 }}>← {t.common.back}</Button>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
          <Typography component="h1" variant="h4" sx={{ fontWeight: 700 }}>{t.signing.title}</Typography>
          <Chip label={t.common.simulationBadge} color="warning" variant="outlined" />
        </Stack>
      </Box>

      <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3.5 } }}>
        <Stack spacing={2.5}>
          <OfferTerms application={app} products={products.data} title="Условия" />
          <Button variant="text" onClick={() => setContractOpen(true)} sx={{ alignSelf: 'flex-start' }}>
            {t.signing.contractLink}
          </Button>
          <FormControlLabel
            control={<Checkbox checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />}
            label={t.signing.confirmCheckbox}
          />
          {sign.error && <ErrorAlert error={sign.error} />}
          <Button
            variant="contained"
            size="large"
            disabled={!agreed || sign.isPending}
            onClick={() => sign.mutate()}
            sx={{ alignSelf: 'flex-start' }}
          >
            {sign.isPending ? t.signing.signing : t.signing.sign}
          </Button>
          <Alert severity="info" variant="outlined">{t.signing.simulationNote}</Alert>
        </Stack>
      </Paper>

      <Dialog open={contractOpen} onClose={() => setContractOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Кредитный договор (образец)</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5}>
            <Typography>Заёмщик: {app.company_name ?? '—'}</Typography>
            {offer && (
              <>
                <Typography>Продукт: {productName || '—'}</Typography>
                <Typography>Сумма кредита: {formatRub(offer.terms.amount)}, срок: {offer.terms.term_months} {t.common.months}</Typography>
                <Typography>
                  Процентная ставка: {offer.terms.annual_rate_percent
                    ? `${offer.terms.annual_rate_percent.replace('.', ',')}% годовых` : t.decision.terms.rateTbd}
                </Typography>
              </>
            )}
            <Typography variant="body2" color="text.secondary">
              Демонстрационный документ прототипа: юридической силы не имеет, данные синтетические.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setContractOpen(false)}>{t.common.close}</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
