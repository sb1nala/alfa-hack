import type { ReactNode } from 'react';
import {
  Box, Button, Chip, CircularProgress, Grid, Paper, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from '@mui/material';
import { Link as RouterLink, useParams } from 'react-router-dom';
import { useUnderwriterApplication, type BankDecision } from '../../api/bank.ts';
import { productById, useProducts } from '../../api/hooks.ts';
import { DecisionForm } from '../../components/bank/DecisionForm.tsx';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { StatusChip } from '../../components/StatusChip.tsx';
import { reviewRecommendation, riskClassColor, riskClassFrom, scoreFromFactors } from '../../lib/bankDecision.ts';
import { formatDate, formatRub } from '../../lib/format.ts';
import { productTexts, t } from '../../lib/texts.ts';

/** Экран 9 (screen-map.md): карточка заявки для аналитика и форма решения. */
export function BankApplicationPage() {
  const { id = '' } = useParams();
  const card = useUnderwriterApplication(id);
  const products = useProducts();

  if (card.isPending) return <CircularProgress aria-label={t.common.loading} />;
  if (card.error) return <ErrorAlert error={card.error} />;

  const { application: app, decisions } = card.data;
  const form = app.form;
  const product = productById(products.data, app.product_id);
  const productName = product ? productTexts(product.code)?.name ?? product.name : '—';
  const latest = decisions.at(-1);
  const scored = [...decisions].reverse().find((d) => d.scoring_factors.length > 0);

  return (
    <Stack spacing={3}>
      <Box>
        <Button component={RouterLink} to="/bank" size="small" sx={{ mb: 1 }}>← {t.bank.queue.title}</Button>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', rowGap: 1 }}>
          <Typography component="h1" variant="h5" sx={{ fontWeight: 700 }}>{app.company_name ?? '—'}</Typography>
          <StatusChip status={app.status} bank />
        </Stack>
      </Box>

      <Grid container spacing={3}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Stack spacing={3}>
            <Section title={t.bank.card.application}>
              <Row label={t.form.inn.label} value={form.company?.inn} />
              <Row label={t.form.legalForm} value={form.company?.legal_form === 'IP' ? t.form.legalFormIP : t.form.legalFormOOO} />
              <Row label={t.form.okved} value={form.company?.okved} />
              <Row label={t.form.address} value={form.company?.address} />
              <Row label={t.form.registrationDate} value={formatDate(form.company?.registration_date)} />
              <Row label={t.decision.terms.product} value={productName} />
              <Row label={t.decision.terms.amount} value={formatRub(app.amount)} />
              <Row label={t.decision.terms.term} value={app.term_months ? `${app.term_months} ${t.common.months}` : '—'} />
              <Row label={t.form.annualRevenue.label} value={formatRub(form.annual_revenue)} />
            </Section>

            <Section title={t.bank.card.scoring}>
              {scored ? <Scoring decision={scored} /> : (
                <Typography color="text.secondary">Скоринг по заявке ещё не проводился.</Typography>
              )}
            </Section>

            <Section title={t.bank.card.history}>
              {decisions.length === 0 ? (
                <Typography color="text.secondary">Решений пока нет.</Typography>
              ) : (
                <Stack spacing={1.5}>
                  {[...decisions].reverse().map((decision) => (
                    <Box key={decision.public.id}>
                      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                        <StatusChip status={decision.public.outcome} />
                        <Typography variant="body2" color="text.secondary">
                          {decision.source === 'underwriter' ? 'Решение аналитика' : 'Автоматическое решение'}
                          {' · '}{formatDate(decision.public.created_at)}
                          {decision.policy_version ? ` · политика ${decision.policy_version}` : ''}
                        </Typography>
                      </Stack>
                      <Typography variant="body2" sx={{ mt: 0.5 }}>{decision.analytical_explanation}</Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </Section>
          </Stack>
        </Grid>

        <Grid size={{ xs: 12, md: 5 }}>
          <Stack spacing={3}>
            {latest && (
              <Section title={t.bank.card.systemRecommendation}>
                <Stack spacing={1}>
                  <StatusChip status={latest.public.outcome} bank />
                  {latest.public.outcome === 'MANUAL_REVIEW' ? (
                    <ReviewReason decision={latest} />
                  ) : (
                    <Typography>{latest.public.explanation}</Typography>
                  )}
                </Stack>
              </Section>
            )}
            {card.data.allowed_actions.includes('decide') ? (
              <DecisionForm card={card.data} products={products.data ?? []} />
            ) : (
              <Paper variant="outlined" sx={{ p: 3 }}>
                <Typography color="text.secondary">
                  Решение выносится, когда заявка находится на ручной проверке.
                </Typography>
              </Paper>
            )}
          </Stack>
        </Grid>
      </Grid>
    </Stack>
  );
}

function Scoring({ decision }: { decision: BankDecision }) {
  const score = scoreFromFactors(decision.scoring_factors);
  const riskClass = riskClassFrom(decision.analytical_explanation);
  const riskColor = riskClassColor(riskClass ?? '');
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1}>
        {score !== null && <Chip label={`${t.bank.card.score}: ${score}`} />}
        {riskClass && <Chip color={riskColor} label={`${t.bank.card.riskClass}: ${riskClass}`}
          sx={riskColor === 'default'
            ? { bgcolor: '#F8F6F9', color: 'text.primary' }
            : { bgcolor: `${riskColor}.light`, color: riskClass === 'D' ? '#592000' : `${riskColor}.dark` }} />}
      </Stack>
      <Table size="small" aria-label={t.bank.card.factors}>
        <TableHead>
          <TableRow>
            <TableCell>{t.bank.card.factors}</TableCell>
            <TableCell>{t.bank.card.factorValue}</TableCell>
            <TableCell align="right">{t.bank.card.factorContribution}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {decision.scoring_factors.map((factor) => {
            const value = Number(factor.contribution);
            return (
              <TableRow key={factor.code}>
                <TableCell>{factor.explanation}</TableCell>
                <TableCell>{factor.value}</TableCell>
                <TableCell
                  align="right"
                  sx={{ fontWeight: 600, color: value > 0 ? 'success.main' : value < 0 ? 'error.main' : 'text.secondary' }}
                >
                  {factor.contribution ?? '—'}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <Typography variant="body2" color="text.secondary">{decision.analytical_explanation}</Typography>
    </Stack>
  );
}

/** Почему заявка на ручной проверке: заголовок и пояснение из словаря продукта. */
function ReviewReason({ decision }: { decision: BankDecision }) {
  const { title, text } = reviewRecommendation(decision.review_reason, decision.analytical_explanation);
  return (
    <>
      {title && <Typography sx={{ fontWeight: 600 }}>{title}</Typography>}
      <Typography color={title ? 'text.secondary' : undefined}>{text}</Typography>
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3 } }}>
      <Typography variant="h6" component="h2" sx={{ mb: 2 }}>{title}</Typography>
      {children}
    </Paper>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0, sm: 2 }} sx={{ py: 0.5 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 220 }}>{label}</Typography>
      <Typography variant="body2">{value || '—'}</Typography>
    </Stack>
  );
}
