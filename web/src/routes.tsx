import { createBrowserRouter } from 'react-router-dom';
import { OpsHome } from './pages/OpsHome';
import { PlanGrid } from './pages/PlanGrid';
import { PlanAdd } from './pages/PlanAdd';
import { PlanRevs } from './pages/PlanRevs';
import { PurchaseHome } from './pages/purchase/PurchaseHome';
import { PurchasePlaceholder } from './pages/PurchasePlaceholder';

export const router = createBrowserRouter([
  { path: '/', element: <OpsHome /> },
  { path: '/plans/:planId', element: <PlanGrid /> },
  { path: '/plans/:planId/add', element: <PlanAdd /> },
  { path: '/plans/:planId/revs', element: <PlanRevs /> },
  { path: '/purchase', element: <PurchaseHome /> },
  { path: '/purchase/new', element: <PurchasePlaceholder crumb="新建采购单" /> },
  { path: '/purchase/:poId', element: <PurchasePlaceholder crumb="采购单" /> },
]);
