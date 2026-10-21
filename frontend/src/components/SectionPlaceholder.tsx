import { Box, Chip, Paper, Stack, Typography } from '@mui/material';

type SectionPlaceholderProps = {
  title: string;
  description: string;
};

export function SectionPlaceholder({ title, description }: SectionPlaceholderProps) {
  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="overline" color="text.secondary">Кредитный кабинет</Typography>
        <Typography component="h1" variant="h4" sx={{ mt: 1, fontWeight: 700 }}>{title}</Typography>
      </Box>
      <Paper variant="outlined" sx={{ p: { xs: 3, md: 5 }, borderTop: '4px solid', borderTopColor: 'secondary.main' }}>
        <Stack spacing={2} sx={{ alignItems: 'flex-start' }}>
          <Chip label="Раздел в разработке" variant="outlined" />
          <Typography variant="h6" component="h2">Здесь будет Ваш рабочий кабинет</Typography>
          <Typography color="text.secondary" sx={{ maxWidth: 650 }}>{description}</Typography>
        </Stack>
      </Paper>
    </Stack>
  );
}
