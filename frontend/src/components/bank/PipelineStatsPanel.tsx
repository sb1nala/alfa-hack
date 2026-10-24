import { Box, Grid, Paper, Skeleton, Stack, Tooltip, Typography } from '@mui/material';
import { usePipelineStats, type PipelineStats } from '../../api/bank.ts';
import { formatRub } from '../../lib/format.ts';
import { formatDuration, plural, share } from '../../lib/stats.ts';
import { fill, statusTexts, t } from '../../lib/texts.ts';

const texts = t.bank.stats;

/**
 * «Конвейер сейчас» над очередью аналитика: четыре плитки и заявки по этапам.
 * Числа — главное, поэтому плитки, а не графики; полосы этапов одного цвета,
 * значение подписано текстом рядом (docs/product/design-tokens.md, dataviz).
 */
export function PipelineStatsPanel() {
  const stats = usePipelineStats();
  if (stats.error) return null; // Очередь важнее сводки: сбой сводки не мешает работе.
  return (
    <Stack spacing={2} component="section" aria-label={texts.title}>
      <Typography variant="h6" component="h2" sx={{ fontWeight: 600 }}>{texts.title}</Typography>
      {stats.data ? <Content stats={stats.data} /> : <Skeleton variant="rounded" height={120} />}
    </Stack>
  );
}

function Content({ stats }: { stats: PipelineStats }) {
  const automated = share(stats.automated, stats.decided);
  const oldest = stats.manual_queue.oldest_waiting_seconds;
  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
        <Tile label={texts.inProgress} value={String(stats.in_progress)} hint={texts.inProgressHint} />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
        <Tile
          label={texts.manualQueue}
          value={String(stats.manual_queue.count)}
          hint={oldest === null ? texts.queueEmpty : fill(texts.oldestWaiting, { time: formatDuration(oldest) })}
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
        <Tile
          label={texts.automated}
          value={automated === null ? '—' : `${automated}%`}
          hint={stats.decided
            ? fill(texts.automatedHint, { automated: stats.automated, decided: stats.decided })
            : texts.noData}
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
        <Tile
          label={texts.decisionTime}
          value={formatDuration(stats.decision_time.median_seconds)}
          hint={stats.decision_time.p90_seconds === null
            ? texts.noData
            : fill(texts.decisionTimeHint, { p90: formatDuration(stats.decision_time.p90_seconds) })}
        />
      </Grid>
      <Grid size={{ xs: 12, md: 8 }}>
        <ByStatus stats={stats} />
      </Grid>
      <Grid size={{ xs: 12, md: 4 }}>
        <Tile
          label={texts.issued}
          value={String(stats.issued.count)}
          hint={fill(texts.issuedHint, { amount: formatRub(stats.issued.amount) })}
        />
      </Grid>
    </Grid>
  );
}

/** Плитка: подпись, крупное значение, пояснение. Текст — только цвета текста, не данных. */
function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 2.5, height: '100%' }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography sx={{ fontSize: 32, fontWeight: 600, lineHeight: 1.25, my: 0.5 }}>{value}</Typography>
      <Typography variant="body2" color="text.secondary">{hint}</Typography>
    </Paper>
  );
}

/** Один ряд, одна величина: полосы одного цвета от общей нуля, число подписано справа. */
function ByStatus({ stats }: { stats: PipelineStats }) {
  const max = Math.max(1, ...stats.by_status.map((item) => item.count));
  return (
    <Paper variant="outlined" sx={{ p: 2.5, height: '100%' }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline', mb: 1.5 }}>
        <Typography variant="body2" color="text.secondary">{texts.byStatus}</Typography>
        <Typography variant="caption" color="text.secondary">{texts.byStatusHint}</Typography>
      </Stack>
      <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'minmax(120px, max-content) 1fr 3ch', gap: '6px 12px', alignItems: 'center' }}>
        {stats.by_status.map((item) => {
          const label = statusTexts(item.status).bank;
          return (
            <Tooltip key={item.status} title={`${label}: ${item.count} ${plural(item.count, texts.countForms)}`} placement="top" arrow>
              <Box sx={{ display: 'contents' }}>
                <Typography component="dt" variant="body2">{label}</Typography>
                <Box aria-hidden sx={{ height: 8 }}>
                  <Box
                    sx={{
                      height: '100%',
                      width: `${(item.count / max) * 100}%`,
                      minWidth: item.count ? 4 : 0,
                      bgcolor: 'secondary.main',
                      borderRadius: '0 4px 4px 0',
                    }}
                  />
                </Box>
                <Typography component="dd" variant="body2" sx={{ m: 0, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  {item.count}
                </Typography>
              </Box>
            </Tooltip>
          );
        })}
      </Box>
    </Paper>
  );
}
