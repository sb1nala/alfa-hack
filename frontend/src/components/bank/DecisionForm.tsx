import { useState } from 'react';
import {
  Alert, Button, Checkbox, FormControl, FormControlLabel, FormGroup, FormHelperText, FormLabel,
  InputAdornment, MenuItem, Paper, Radio, RadioGroup, Snackbar, Stack, TextField, Typography,
} from '@mui/material';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, ApiProtocolError } from '../../api/index.ts';
import { bankKeys, type UnderwriterApplication } from '../../api/bank.ts';
import { keys, type Product } from '../../api/hooks.ts';
import { errorMessage } from '../ErrorAlert.tsx';
import {
  buildDecisionRequest, counterReasonCodes, counterReasonText, defaultExplanation, documentCodes,
  documentText, rejectionCodes, rejectionText, validateDraft, type DecisionDraft, type DraftErrors,
  type ManualOutcome,
} from '../../lib/bankDecision.ts';
import { formatAmountInput, moneyToInput } from '../../lib/format.ts';
import { productTexts, t } from '../../lib/texts.ts';

const OUTCOMES: { value: ManualOutcome; label: string }[] = [
  { value: 'APPROVED', label: t.bank.decision.approve },
  { value: 'COUNTER_OFFER', label: t.bank.decision.counterOffer },
  { value: 'NEEDS_INFO', label: t.bank.decision.needsInfo },
  { value: 'REJECTED', label: t.bank.decision.reject },
];

/** Начальные условия — из последнего предложения, иначе из анкеты. */
function initialDraft(card: UnderwriterApplication): DecisionDraft {
  const offer = [...card.decisions].reverse()
    .map((d) => ('offer' in d.public ? d.public.offer : null))
    .find(Boolean);
  const app = card.application;
  const draft: DecisionDraft = {
    outcome: 'APPROVED',
    productId: offer?.terms.product_id ?? app.product_id ?? '',
    amount: moneyToInput(offer?.terms.amount ?? app.amount),
    term: String(offer?.terms.term_months ?? app.term_months ?? ''),
    rate: offer?.terms.annual_rate_percent?.replace('.', ',') ?? '',
    counterReasons: [],
    rejectReason: '',
    documents: [],
    explanation: '',
    analytical: '',
  };
  return { ...draft, explanation: defaultExplanation(draft) };
}

/** Форма решения аналитика (screen-map.md, экран 9): 4 исхода из MANUAL_REVIEW. */
export function DecisionForm({ card, products }: { card: UnderwriterApplication; products: Product[] }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<DecisionDraft>(() => initialDraft(card));
  const [errors, setErrors] = useState<DraftErrors>({});
  const [explanationEdited, setExplanationEdited] = useState(false);
  const [done, setDone] = useState(false);

  /** Пока аналитик не правил текст для клиента, он следует за исходом и причинами. */
  function update(patch: Partial<DecisionDraft>) {
    setDraft((prev) => {
      const next = { ...prev, ...patch };
      return explanationEdited ? next : { ...next, explanation: defaultExplanation(next) };
    });
  }

  const decide = useMutation({
    mutationFn: async () => {
      const { data } = await api.http.POST('/api/underwriter/applications/{application_id}/decision', {
        params: { path: { application_id: card.application.id } },
        body: buildDecisionRequest(draft, card.application.revision),
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(bankKeys.card(data.application.id), data);
      void queryClient.invalidateQueries({ queryKey: ['underwriter', 'queue'] });
      void queryClient.invalidateQueries({ queryKey: keys.application(data.application.id) });
      setDone(true);
      window.setTimeout(() => navigate('/bank'), 1_200);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: bankKeys.card(card.application.id) });
      }
    },
  });

  function submit() {
    // Продукт из прошлого предложения, которого нет в каталоге, не отправляем.
    const known = products.some((p) => p.id === draft.productId);
    const found = validateDraft(known ? draft : { ...draft, productId: '' });
    setErrors(found);
    if (Object.keys(found).length === 0) decide.mutate();
  }

  const withOffer = draft.outcome === 'APPROVED' || draft.outcome === 'COUNTER_OFFER';
  const toggle = (list: string[], code: string) =>
    list.includes(code) ? list.filter((c) => c !== code) : [...list, code];

  return (
    <Paper variant="outlined" sx={{ p: { xs: 2.5, md: 3 } }}>
      <Stack spacing={2.5}>
        <Typography variant="h6" component="h2">{t.bank.decision.title}</Typography>

        <RadioGroup
          value={draft.outcome}
          onChange={(e) => update({ outcome: e.target.value as ManualOutcome })}
          aria-label={t.bank.decision.title}
        >
          {OUTCOMES.map((o) => (
            <FormControlLabel key={o.value} value={o.value} control={<Radio />} label={o.label} />
          ))}
        </RadioGroup>

        {withOffer && (
          <Stack spacing={2}>
            <TextField
              select
              label={t.decision.terms.product}
              value={products.some((p) => p.id === draft.productId) ? draft.productId : ''}
              onChange={(e) => update({ productId: e.target.value })}
              error={Boolean(errors.product)}
              helperText={errors.product}
            >
              {products.map((p) => (
                <MenuItem key={p.id} value={p.id}>{productTexts(p.code)?.name ?? p.name}</MenuItem>
              ))}
            </TextField>
            <TextField
              label={t.decision.terms.amount}
              value={draft.amount}
              onChange={(e) => update({ amount: formatAmountInput(e.target.value) })}
              error={Boolean(errors.amount)}
              helperText={errors.amount}
              slotProps={{ input: { endAdornment: <InputAdornment position="end">{t.common.currency}</InputAdornment> } }}
            />
            <TextField
              label={t.decision.terms.term}
              value={draft.term}
              onChange={(e) => update({ term: e.target.value.replace(/\D/g, '').slice(0, 3) })}
              error={Boolean(errors.term)}
              helperText={errors.term}
              slotProps={{ input: { endAdornment: <InputAdornment position="end">{t.common.months}</InputAdornment> } }}
            />
            <TextField
              label={t.decision.terms.rate}
              value={draft.rate}
              onChange={(e) => update({ rate: e.target.value.replace(/[^\d.,]/g, '').slice(0, 6) })}
              error={Boolean(errors.rate)}
              helperText={errors.rate ?? 'Пусто — ставку сообщим при подписании'}
              slotProps={{ input: { endAdornment: <InputAdornment position="end">% годовых</InputAdornment> } }}
            />
          </Stack>
        )}

        {draft.outcome === 'COUNTER_OFFER' && (
          <FormControl error={Boolean(errors.reasons)}>
            <FormLabel>{t.decision.counterOffer.whyTitle}</FormLabel>
            <FormGroup>
              {counterReasonCodes.map((code) => (
                <FormControlLabel
                  key={code}
                  control={<Checkbox checked={draft.counterReasons.includes(code)}
                    onChange={() => update({ counterReasons: toggle(draft.counterReasons, code) })} />}
                  label={counterReasonText(code)}
                />
              ))}
            </FormGroup>
            {errors.reasons && <FormHelperText>{errors.reasons}</FormHelperText>}
          </FormControl>
        )}

        {draft.outcome === 'REJECTED' && (
          <TextField
            select
            label={t.bank.decision.clientReasonLabel}
            value={draft.rejectReason}
            onChange={(e) => update({ rejectReason: e.target.value })}
            error={Boolean(errors.reasons)}
            helperText={errors.reasons}
          >
            {rejectionCodes.map((code) => (
              <MenuItem key={code} value={code} sx={{ whiteSpace: 'normal' }}>{rejectionText(code)?.message}</MenuItem>
            ))}
          </TextField>
        )}

        {draft.outcome === 'NEEDS_INFO' && (
          <FormControl error={Boolean(errors.documents)}>
            <FormLabel>{t.decision.needsInfo.listTitle}</FormLabel>
            <FormGroup>
              {documentCodes.map((code) => (
                <FormControlLabel
                  key={code}
                  control={<Checkbox checked={draft.documents.includes(code)}
                    onChange={() => update({ documents: toggle(draft.documents, code) })} />}
                  label={documentText(code)?.title ?? code}
                />
              ))}
            </FormGroup>
            {errors.documents && <FormHelperText>{errors.documents}</FormHelperText>}
          </FormControl>
        )}

        <TextField
          label="Текст для клиента"
          multiline
          minRows={3}
          value={draft.explanation}
          onChange={(e) => { setExplanationEdited(true); setDraft((prev) => ({ ...prev, explanation: e.target.value })); }}
          error={Boolean(errors.explanation)}
          helperText={errors.explanation ?? 'Клиент увидит этот текст в кабинете'}
        />
        <TextField
          label={t.bank.decision.auditCommentLabel}
          multiline
          minRows={3}
          required
          value={draft.analytical}
          onChange={(e) => setDraft((prev) => ({ ...prev, analytical: e.target.value }))}
          error={Boolean(errors.analytical)}
          helperText={errors.analytical ?? t.bank.decision.auditCommentHint}
        />

        {decide.error && <Alert severity="error">{errorMessage(decide.error)}</Alert>}
        <Button variant="contained" size="large" onClick={submit} disabled={decide.isPending || done}>
          {t.bank.decision.submit}
        </Button>
      </Stack>
      <Snackbar open={done} message={t.bank.decision.done} anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }} />
    </Paper>
  );
}
