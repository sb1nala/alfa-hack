import { useState } from 'react';
import {
  Alert, Box, Button, Chip, InputAdornment, Stack, TextField, Typography,
} from '@mui/material';
import { useQueryClient } from '@tanstack/react-query';
import { api, ApiProtocolError, type components } from '../../api/index.ts';
import { uploadDocument } from '../../api/documents.ts';
import { keys, type ClientApplication } from '../../api/hooks.ts';
import { errorMessage } from '../ErrorAlert.tsx';
import {
  ALLOWED_TYPES, buildAdditionalInfo, fileError, missingItems, REVENUE_ITEM,
} from '../../lib/clientActions.ts';
import { formatAmountInput } from '../../lib/format.ts';
import { t } from '../../lib/texts.ts';

type RequiredDocument = components['schemas']['RequiredDocument'];
type UploadedDocument = components['schemas']['Document'];

/**
 * NEEDS_INFO (screen-map.md, экран 7): по каждому пункту — загрузка файла, для выручки —
 * поле ввода; затем «Отправить данные». Каждая загрузка меняет ревизию заявки, поэтому
 * перед запросом берём актуальную ревизию с сервера.
 */
export function NeedsInfoForm({ application, documents }: { application: ClientApplication; documents: RequiredDocument[] }) {
  const queryClient = useQueryClient();
  const [uploaded, setUploaded] = useState<Record<string, UploadedDocument>>({});
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [revenue, setRevenue] = useState('');
  const [comment, setComment] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function latestRevision(): Promise<number> {
    const { data } = await api.http.GET('/api/applications/{application_id}', {
      params: { path: { application_id: application.id } },
    });
    if (!data) throw new ApiProtocolError();
    queryClient.setQueryData(keys.application(data.id), data);
    return data.revision;
  }

  async function onFile(code: string, file: File | undefined) {
    if (!file) return;
    const invalid = fileError(file);
    if (invalid) {
      setError(invalid);
      return;
    }
    setError(null);
    setBusyItem(code);
    try {
      const revision = await latestRevision();
      const { data } = await uploadDocument(api, application.id, {
        document_type: code,
        expected_revision: revision,
        file,
      });
      if (!data) throw new ApiProtocolError();
      setUploaded((prev) => ({ ...prev, [code]: data }));
      await latestRevision();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusyItem(null);
    }
  }

  const required = documents.map((d) => d.code);
  const missing = missingItems(required, uploaded, revenue);

  async function submit() {
    if (missing.length) {
      setError(t.errors.validation);
      return;
    }
    setError(null);
    setSending(true);
    try {
      const body = buildAdditionalInfo(
        { comment, documentIds: Object.values(uploaded).map((d) => d.id), annualRevenue: required.includes(REVENUE_ITEM) ? revenue : '' },
        await latestRevision(),
      );
      const { data } = await api.http.POST('/api/applications/{application_id}/additional-info', {
        params: { path: { application_id: application.id } },
        body,
      });
      if (!data) throw new ApiProtocolError();
      queryClient.setQueryData(keys.application(data.id), data);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <Stack spacing={2.5}>
      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 600 }}>{t.decision.needsInfo.listTitle}</Typography>
      {documents.map((doc) => (
        <Box key={doc.code} sx={{ borderLeft: '3px solid', borderLeftColor: 'info.main', pl: 2 }}>
          <Typography sx={{ fontWeight: 500 }}>{doc.title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t.decision.needsInfo.why}: {doc.explanation}
          </Typography>
          {doc.code === REVENUE_ITEM ? (
            <TextField
              fullWidth
              size="small"
              label={t.form.annualRevenue.label}
              value={revenue}
              onChange={(e) => setRevenue(formatAmountInput(e.target.value))}
              slotProps={{
                htmlInput: { inputMode: 'numeric' },
                input: { endAdornment: <InputAdornment position="end">{t.common.currency}</InputAdornment> },
              }}
            />
          ) : (
            <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <Button variant="outlined" component="label" disabled={busyItem !== null || sending}>
                {busyItem === doc.code ? t.common.loading : t.decision.needsInfo.upload}
                <input
                  hidden
                  type="file"
                  accept={ALLOWED_TYPES.join(',')}
                  onChange={(e) => { void onFile(doc.code, e.target.files?.[0]); e.target.value = ''; }}
                />
              </Button>
              {uploaded[doc.code] && (
                <Chip color="success" variant="outlined" label={`${t.decision.needsInfo.uploaded}: ${uploaded[doc.code]?.filename}`} />
              )}
            </Stack>
          )}
        </Box>
      ))}
      <Typography variant="caption" color="text.secondary">{t.decision.needsInfo.fileHint}</Typography>
      <TextField
        label={t.decision.needsInfo.commentLabel}
        multiline
        minRows={2}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
      />
      {error && <Alert severity="error">{error}</Alert>}
      <Button
        variant="contained"
        size="large"
        onClick={submit}
        disabled={sending || busyItem !== null}
        sx={{ alignSelf: 'flex-start' }}
      >
        {t.decision.needsInfo.submit}
      </Button>
    </Stack>
  );
}
