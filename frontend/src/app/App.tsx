import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '../components/AppLayout';
import { BankPage } from '../pages/BankPage';
import { LoginPage } from '../pages/LoginPage';
import { NotFoundPage } from '../pages/NotFoundPage';
import { ApplicationFormPage } from '../pages/client/ApplicationFormPage';
import { ApplicationPage } from '../pages/client/ApplicationPage';
import { ApplicationsPage } from '../pages/client/ApplicationsPage';
import { NewApplicationPage } from '../pages/client/NewApplicationPage';
import { useMe } from '../api/hooks';
import { homeOf, RequireRole } from './RequireRole';

/** Маршруты — docs/product/screen-map.md. */
export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Home />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="client" element={<RequireRole role="client"><ApplicationsPage /></RequireRole>} />
        <Route path="client/new" element={<RequireRole role="client"><NewApplicationPage /></RequireRole>} />
        <Route
          path="client/applications/:id"
          element={<RequireRole role="client"><ApplicationPage /></RequireRole>}
        />
        <Route
          path="client/applications/:id/edit"
          element={<RequireRole role="client"><ApplicationFormPage /></RequireRole>}
        />
        <Route path="bank" element={<RequireRole role="underwriter"><BankPage /></RequireRole>} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

function Home() {
  const me = useMe();
  if (me.isPending) return null;
  return <Navigate to={me.data ? homeOf(me.data.role) : '/login'} replace />;
}
