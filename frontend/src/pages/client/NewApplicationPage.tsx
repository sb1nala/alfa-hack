import { useState } from 'react';
import {
  Box, Button, Card, CardActionArea, Chip, CircularProgress, Grid, Paper, Stack, Typography,
} from '@mui/material';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiProtocolError } from '../../api/index.ts';
import { keys, useProducts, type Product } from '../../api/hooks.ts';
import { ErrorAlert } from '../../components/ErrorAlert.tsx';
import { productTexts, purposeCodes, purposeTexts, t, type PurposeCode } from '../../lib/texts.ts';

/** Экран 3 (screen-map.md): сначала задача, потом продукт. По «Выбрать» создаётся черновик. */
export function NewApplicationPage() {
  const products = useProducts();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [purpose, setPurpose] = useState<PurposeCode | null>(null);

  const create = useMutation({
    mutationFn: async (product: Product) => {
      const { data } = await api.http.POST('/api/applications', {
        body: { product_id: product.id, purpose },
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    onSuccess: (application) => {
      queryClient.setQueryData(keys.application(application.id), application);
      void queryClient.invalidateQueries({ queryKey: keys.applications });
      navigate(`/client/applications/${application.id}/edit`);
    },
  });

  const suitable = purpose
    ? (purposeTexts(purpose)?.products ?? [])
      .map((code) => products.data?.find((p) => p.code === code))
      .filter((p): p is Product => Boolean(p))
    : [];

  return (
    <Stack spacing={4}>
      <Box>
        <Typography component="h1" variant="h4" sx={{ fontWeight: 700 }}>{t.productPicker.title}</Typography>
        <Typography color="text.secondary" sx={{ mt: 1 }}>{t.productPicker.subtitle}</Typography>
      </Box>

      <Grid container spacing={2} role="radiogroup" aria-label={t.productPicker.title}>
        {purposeCodes.map((code) => {
          const selected = code === purpose;
          return (
            <Grid key={code} size={{ xs: 12, sm: 6, md: 4 }}>
              <Card
                variant="outlined"
                sx={{
                  height: '100%',
                  borderColor: selected ? 'secondary.main' : 'divider',
                  borderWidth: selected ? 2 : 1,
                  bgcolor: selected ? 'success.light' : 'background.paper',
                }}
              >
                <CardActionArea
                  role="radio"
                  aria-checked={selected}
                  aria-label={purposeTexts(code)?.label ?? code}
                  onClick={() => setPurpose(code)}
                  sx={{ p: 2.5, height: '100%' }}
                >
                  <Typography sx={{ fontWeight: 600 }}>{purposeTexts(code)?.label}</Typography>
                </CardActionArea>
              </Card>
            </Grid>
          );
        })}
      </Grid>

      {products.isPending && purpose && <CircularProgress aria-label={t.common.loading} />}
      {products.error && <ErrorAlert error={products.error} />}
      {create.error && <ErrorAlert error={create.error} />}

      {suitable.length > 0 && (
        <Stack spacing={2}>
          {suitable.map((product, index) => {
            const text = productTexts(product.code);
            return (
              <Paper
                key={product.id}
                variant="outlined"
                sx={{ p: 3, borderLeft: '4px solid', borderLeftColor: index === 0 ? 'secondary.main' : 'divider' }}
              >
                <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ justifyContent: 'space-between' }}>
                  <Stack spacing={1}>
                    <Chip
                      size="small"
                      label={index === 0 ? t.productPicker.recommended : t.productPicker.alsoFits}
                      color={index === 0 ? 'secondary' : 'default'}
                      sx={{ alignSelf: 'flex-start' }}
                    />
                    <Typography variant="h6" component="h2">{text?.name ?? product.name}</Typography>
                    <Typography color="text.secondary">{text?.description ?? product.description}</Typography>
                    {text?.params && <Typography variant="body2" sx={{ fontWeight: 500 }}>{text.params}</Typography>}
                  </Stack>
                  <Box sx={{ alignSelf: { md: 'center' } }}>
                    <Button
                      variant={index === 0 ? 'contained' : 'outlined'}
                      size="large"
                      disabled={create.isPending}
                      onClick={() => create.mutate(product)}
                      aria-label={`${t.productPicker.choose}: ${text?.name ?? product.name}`}
                    >
                      {t.productPicker.choose}
                    </Button>
                  </Box>
                </Stack>
              </Paper>
            );
          })}
        </Stack>
      )}
    </Stack>
  );
}
