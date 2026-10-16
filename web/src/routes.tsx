import { createBrowserRouter } from 'react-router-dom';
import { OpsHome } from './pages/OpsHome';
import { PlanGrid } from './pages/PlanGrid';
import { PlanAdd } from './pages/PlanAdd';
import { PlanRevs } from './pages/PlanRevs';
import { PurchaseHome } from './pages/purchase/PurchaseHome';
import { PoEditor, PoRoute } from './pages/purchase/PoEditor';
import { DispatchHome } from './pages/dispatch/DispatchHome';
import { SheetPage } from './pages/dispatch/SheetPage';

export const router = createBrowserRouter([
  { path: '/', element: <OpsHome /> },
  { path: '/plans/:planId', element: <PlanGrid /> },
  { path: '/plans/:planId/add', element: <PlanAdd /> },
  { path: '/plans/:planId/revs', element: <PlanRevs /> },
  { path: '/purchase', element: <PurchaseHome /> },
  { path: '/purchase/new', element: <PoEditor key="new" poId={null} /> },
  // 草稿 → 编辑器；其余暂走占位（详情页另做）
  { path: '/purchase/:poId', element: <PoRoute /> },
  { path: '/dispatch', element: <DispatchHome /> },
  { path: '/dispatch/sheets/:sheetId', element: <SheetPage key="sheet" /> },
]);
