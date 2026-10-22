import { Chip, type ChipProps } from '@mui/material';
import { statusTexts, type ApplicationStatus } from '../lib/texts.ts';

/** Цвета исходов — docs/product/design-tokens.md, раздел «Исходы и статусы». */
export function statusColor(status: ApplicationStatus): ChipProps['color'] {
  switch (status) {
    case 'APPROVED':
    case 'OFFER_ACCEPTED':
    case 'SIGNED':
    case 'ISSUED':
      return 'success';
    case 'COUNTER_OFFER':
      return 'warning';
    case 'NEEDS_INFO':
    case 'MANUAL_REVIEW':
      return 'info';
    case 'REJECTED':
      return 'error';
    default:
      return 'default';
  }
}

export function StatusChip({ status, bank = false }: { status: ApplicationStatus; bank?: boolean }) {
  const color = statusColor(status);
  return (
    <Chip
      size="small"
      label={bank ? statusTexts(status).bank : statusTexts(status).timeline}
      color={color}
      variant={color === 'default' ? 'outlined' : 'filled'}
      sx={color === 'default' ? undefined : {
        bgcolor: `${color}.light`,
        color: `${color}.dark`,
        fontWeight: 500,
      }}
    />
  );
}
