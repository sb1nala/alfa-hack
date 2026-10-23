import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, InputAdornment, MenuItem, Paper, Stack,
  TextField, Typography,
} from '@mui/material';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, ApiProtocolError, type components } from '../../api/index.ts';
import {
  keys, newIdempotencyKey, productById, useApplication, useProducts, type ClientApplication,
} from '../../api/hooks.ts';
import { ErrorAlert, errorMessage } from '../../components/ErrorAlert.tsx';
import { formatAmountInput, formatDate, formatNumber, moneyToInput, monthlyPayment, toMoney } from '../../lib/format.ts';
import { isValidInn, legalFormByInn } from '../../lib/inn.ts';
import { productRule } from '../../lib/products.ts';
import { fill, productTexts, t } from '../../lib/texts.ts';

type CompanyInput = components['schemas']['CompanyInput'];
type Field = 'inn' | 'name' | 'amount' | 'term' | 'revenue';
type Errors = Partial<Record<Field, string>>;

/** Поля API → поля формы, чтобы показать field_errors сервера у нужного поля. */
const SERVER_FIELDS: Record<string, Field> = {
  'body.company.inn': 'inn',
  'body.company.name': 'name',
  'body.amount': 'amount',
  'body.term_months': 'term',
  'body.annual_revenue': 'revenue',
};

/** Экран 4 (screen-map.md): анкета черновика — компания, кредит, отправка. */
export function ApplicationFormPage() {
  const { id = '' } = useParams();
  const application = useApplication(id);

  if (application.isPending) return <CircularProgress aria-label={t.common.loading} />;
  if (application.error) return <ErrorAlert error={application.error} />;
  if (!application.data.allowed_actions.includes('edit')) {
    return <Navigate to={`/client/applications/${id}`} replace />;
  }
  return <DraftForm key={application.data.id} application={application.data} />;
}

function DraftForm({ application }: { application: ClientApplication }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const products = useProducts();
  const product = productById(products.data, application.product_id);
  const rule = productRule(product?.code);
  const productName = product ? productTexts(product.code)?.name ?? product.name : '';

  const form = application.form;
  const [inn, setInn] = useState(form.company?.inn ?? '');
  const [manualName, setManualName] = useState(form.company?.name ?? '');
  const [manualForm, setManualForm] = useState<'OOO' | 'IP'>(form.company?.legal_form ?? 'OOO');
  const [amount, setAmount] = useState(moneyToInput(form.amount));
  const [term, setTerm] = useState(form.term_months ? String(form.term_months) : '');
  const [revenue, setRevenue] = useState(moneyToInput(form.annual_revenue));
  const [errors, setErrors] = useState<Errors>({});
  const [notice, setNotice] = useState<{ severity: 'success' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState<'save' | 'submit' | null>(null);
  const revision = useRef(application.revision);
  const clearError = (field: Field) => setErrors((prev) => ({ ...prev, [field]: undefined }));
  // Один ключ на попытку отправки: повторный клик не создаст вторую отправку.
  const idempotencyKey = useRef(newIdempotencyKey());

  const innValid = isValidInn(inn);
  const lookup = useQuery({
    queryKey: keys.lookup(inn),
    enabled: innValid,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const { data } = await api.http.GET('/api/companies/lookup', { params: { query: { inn } }, signal });
      if (!data) throw new ApiProtocolError();
      return data.company;
    },
  });
  const found = innValid ? lookup.data ?? null : null;
  const notFound = innValid && lookup.isSuccess && !lookup.data;

  useEffect(() => {
    const legalForm = legalFormByInn(inn);
    if (legalForm) setManualForm(legalForm);
  }, [inn]);

  const company: CompanyInput | null = useMemo(() => {
    if (!innValid) return null;
    if (found) return found;
    if (notFound && manualName.trim()) return { inn, name: manualName.trim(), legal_form: manualForm };
    return null;
  }, [innValid, found, notFound, manualName, manualForm, inn]);

  const amountNumber = Number(toMoney(amount) ?? 0);
  const termNumber = Number(term) || 0;
  const payment = rule ? monthlyPayment(amountNumber, termNumber, rule.baseRatePercent) : 0;

  /** Границы продукта проверяем сразу при вводе; обязательность полей — при сохранении/отправке. */
  function rangeErrors(): Errors {
    const next: Errors = {};
    if (inn.length >= 10 && !innValid) next.inn = t.form.inn.errorFormat;
    if (rule && amount && (amountNumber < rule.minAmount || amountNumber > rule.maxAmount)) {
      next.amount = fill(t.form.amount.errorRange, {
        min: formatNumber(rule.minAmount),
        max: formatNumber(rule.maxAmount),
      });
    }
    if (rule && term && (termNumber < rule.minTerm || termNumber > rule.maxTerm)) {
      next.term = fill(t.form.term.errorRange, { min: rule.minTerm, max: rule.maxTerm });
    }
    return next;
  }

  function validate(forSubmit: boolean): Errors {
    const next: Errors = { ...rangeErrors() };
    if (inn && !innValid) next.inn = t.form.inn.errorFormat;
    if (!forSubmit) return next;
    if (!company) next.inn = innValid && notFound ? t.form.inn.errorNotFound : t.form.inn.errorFormat;
    if (notFound && !manualName.trim()) next.name = t.errors.validation;
    if (!amount) next.amount = t.errors.validation;
    if (!term) next.term = t.errors.validation;
    if (!(Number(toMoney(revenue) ?? 0) > 0)) next.revenue = t.errors.validation;
    return next;
  }

  async function save(): Promise<ClientApplication> {
    const { data } = await api.http.PATCH('/api/applications/{application_id}', {
      params: { path: { application_id: application.id } },
      body: {
        expected_revision: revision.current,
        company,
        amount: toMoney(amount),
        term_months: termNumber || null,
        annual_revenue: toMoney(revenue),
      },
    });
    if (!data) throw new ApiProtocolError();
    revision.current = data.revision;
    queryClient.setQueryData(keys.application(data.id), data);
    return data;
  }

  function showServerError(error: unknown) {
    if (error instanceof ApiError && error.fieldErrors.length) {
      const mapped: Errors = {};
      for (const fieldError of error.fieldErrors) {
        const field = SERVER_FIELDS[fieldError.field];
        if (field) mapped[field] = fieldError.message;
      }
      setErrors(mapped);
    }
    if (error instanceof ApiError && error.status === 409) {
      void queryClient.invalidateQueries({ queryKey: keys.application(application.id) });
    }
    setNotice({ severity: 'error', text: errorMessage(error) });
  }

  async function onSave() {
    const found = validate(false);
    setErrors(found);
    setNotice(null);
    if (Object.keys(found).length) return;
    setBusy('save');
    try {
      await save();
      setNotice({ severity: 'success', text: t.form.draftSaved });
    } catch (error) {
      showServerError(error);
    } finally {
      setBusy(null);
    }
  }

  async function onSubmit() {
    const found = validate(true);
    setErrors(found);
    setNotice(null);
    if (Object.keys(found).length) {
      setNotice({ severity: 'error', text: t.errors.validation });
      return;
    }
    setBusy('submit');
    try {
      const saved = await save();
      const { data } = await api.http.POST('/api/applications/{application_id}/submit', {
        params: {
          path: { application_id: application.id },
          header: { 'Idempotency-Key': idempotencyKey.current },
        },
        body: { expected_revision: saved.revision, consent_to_checks: true },
      });
      if (!data) throw new ApiProtocolError();
      queryClient.setQueryData(keys.application(data.id), data);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
      navigate(`/client/applications/${application.id}`, { replace: true });
    } catch (error) {
      idempotencyKey.current = newIdempotencyKey();
      showServerError(error);
    } finally {
      setBusy(null);
    }
  }

  const amountHint = rule
    ? fill(t.form.amount.hint, { min: formatNumber(rule.minAmount), max: formatNumber(rule.maxAmount) })
    : undefined;
  const termHint = rule ? fill(t.form.term.hint, { min: rule.minTerm, max: rule.maxTerm }) : undefined;
  const live = rangeErrors();
  // Ошибка сохранения/отправки важнее живой проверки; очищенные поля (undefined) её не затирают.
  const shown: Errors = { ...live };
  for (const [field, message] of Object.entries(errors) as [Field, string | undefined][]) {
    if (message) shown[field] = message;
  }

  return (
    <Stack spacing={3} sx={{ maxWidth: 760 }}>
      <Box>
        <Typography component="h1" variant="h4" sx={{ fontWeight: 700 }}>{t.form.title}</Typography>
        {productName && <Chip label={productName} color="secondary" variant="outlined"
          sx={{ mt: 1.5, color: 'secondary.dark', borderColor: 'secondary.main' }} />}
      </Box>

      <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 4 } }}>
        <Stack spacing={2.5}>
          <Typography variant="h6" component="h2">1. Компания</Typography>
          <TextField
            label={t.form.inn.label}
            value={inn}
            onChange={(e) => { setInn(e.target.value.replace(/\D/g, '').slice(0, 12)); clearError('inn'); }}
            error={Boolean(shown.inn)}
            helperText={shown.inn ?? (lookup.isFetching ? t.form.inn.searching : t.form.inn.hint)}
            slotProps={{ htmlInput: { inputMode: 'numeric' } }}
          />
          {found && (
            <Stack spacing={1.5}>
              <Alert severity="success" variant="outlined">{t.form.autofillNote}</Alert>
              <ReadOnlyRow label={t.form.companyName} value={found.name} />
              <ReadOnlyRow label={t.form.legalForm} value={found.legal_form === 'IP' ? t.form.legalFormIP : t.form.legalFormOOO} />
              <ReadOnlyRow label={t.form.address} value={found.address} />
              <ReadOnlyRow label={t.form.okved} value={found.okved} />
              <ReadOnlyRow label={t.form.registrationDate} value={formatDate(found.registration_date)} />
            </Stack>
          )}
          {notFound && (
            <Stack spacing={2}>
              <Alert severity="warning">{t.form.inn.errorNotFound}</Alert>
              <TextField
                label={t.form.companyName}
                value={manualName}
                onChange={(e) => { setManualName(e.target.value); clearError('name'); }}
                error={Boolean(errors.name)}
                helperText={errors.name}
              />
              <TextField
                select
                label={t.form.legalForm}
                value={manualForm}
                onChange={(e) => setManualForm(e.target.value as 'OOO' | 'IP')}
              >
                <MenuItem value="OOO">{t.form.legalFormOOO}</MenuItem>
                <MenuItem value="IP">{t.form.legalFormIP}</MenuItem>
              </TextField>
            </Stack>
          )}
          {lookup.error && <ErrorAlert error={lookup.error} />}

          <Divider />
          <Typography variant="h6" component="h2">2. Кредит</Typography>
          <TextField
            label={t.form.amount.label}
            value={amount}
            onChange={(e) => { setAmount(formatAmountInput(e.target.value)); clearError('amount'); }}
            error={Boolean(shown.amount)}
            helperText={shown.amount ?? amountHint}
            slotProps={{
              htmlInput: { inputMode: 'numeric' },
              input: { endAdornment: <InputAdornment position="end">{t.common.currency}</InputAdornment> },
            }}
          />
          <TextField
            label={t.form.term.label}
            value={term}
            onChange={(e) => { setTerm(e.target.value.replace(/\D/g, '').slice(0, 3)); clearError('term'); }}
            error={Boolean(shown.term)}
            helperText={shown.term ?? termHint}
            slotProps={{
              htmlInput: { inputMode: 'numeric' },
              input: { endAdornment: <InputAdornment position="end">{t.common.months}</InputAdornment> },
            }}
          />
          <TextField
            label={t.form.annualRevenue.label}
            value={revenue}
            onChange={(e) => { setRevenue(formatAmountInput(e.target.value)); clearError('revenue'); }}
            error={Boolean(errors.revenue)}
            helperText={errors.revenue ?? t.form.annualRevenue.hint}
            slotProps={{
              htmlInput: { inputMode: 'numeric' },
              input: { endAdornment: <InputAdornment position="end">{t.common.currency}</InputAdornment> },
            }}
          />
          {payment > 0 && !shown.amount && !shown.term && (
            <Alert severity="info" variant="outlined">
              {fill(t.form.estimatedPayment, { payment: formatNumber(Math.round(payment)) })}
            </Alert>
          )}

          <Divider />
          <Typography variant="h6" component="h2">3. Отправка</Typography>
          <Typography variant="body2" color="text.secondary">{t.form.consent}</Typography>
          {notice && <Alert severity={notice.severity}>{notice.text}</Alert>}
          <Stack direction={{ xs: 'column-reverse', sm: 'row' }} spacing={2} sx={{ justifyContent: 'space-between' }}>
            <Button variant="outlined" size="large" onClick={onSave} disabled={busy !== null}>
              {t.form.saveDraft}
            </Button>
            <Button variant="contained" size="large" onClick={onSubmit} disabled={busy !== null}>
              {busy === 'submit' ? t.common.loading : t.form.submit}
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}

function ReadOnlyRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0, sm: 2 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 240 }}>{label}</Typography>
      <Typography variant="body2">{value || '—'}</Typography>
    </Stack>
  );
}
