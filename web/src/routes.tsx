import { createBrowserRouter } from 'react-router-dom';
import { OpsHome } from './pages/OpsHome';
import { PlanGrid } from './pages/PlanGrid';
import { PlanAdd } from './pages/PlanAdd';
import { PlanRevs } from './pages/PlanRevs';

export const router = createBrowserRouter([
  { path: '/', element: <OpsHome /> },
  { path: '/plans/:planId', element: <PlanGrid /> },
  { path: '/plans/:planId/add', element: <PlanAdd /> },
  { path: '/plans/:planId/revs', element: <PlanRevs /> },
]);
