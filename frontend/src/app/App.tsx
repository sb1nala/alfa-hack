import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '../components/AppLayout';
import { BankPage } from '../pages/BankPage';
import { ClientPage } from '../pages/ClientPage';
import { NotFoundPage } from '../pages/NotFoundPage';

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/client" replace />} />
        <Route path="client" element={<ClientPage />} />
        <Route path="bank" element={<BankPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
