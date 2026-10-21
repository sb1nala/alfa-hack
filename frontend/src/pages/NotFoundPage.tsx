import { Button, Stack, Typography } from '@mui/material';
import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <Stack spacing={2} sx={{ alignItems: 'flex-start' }}>
      <Typography component="h1" variant="h4">Страница не найдена</Typography>
      <Typography color="text.secondary">Проверьте адрес или вернитесь в кабинет.</Typography>
      <Button component={Link} to="/client" variant="contained">В кабинет предпринимателя</Button>
    </Stack>
  );
}
