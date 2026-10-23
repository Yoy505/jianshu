import type { ReactElement } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AdminLayout } from '@/components/layout/AdminLayout'
import AuditLogs from '@/pages/AuditLogs'
import CommerceBatches from '@/pages/CommerceBatches'
import CommerceOrders from '@/pages/CommerceOrders'
import CommercePackages from '@/pages/CommercePackages'
import CommercePoints from '@/pages/CommercePoints'
import Dashboard from '@/pages/Dashboard'
import Login from '@/pages/Login'
import MpAppointments from '@/pages/MpAppointments'
import MpConsults from '@/pages/MpConsults'
import MpDrugs from '@/pages/MpDrugs'
import MpProviders from '@/pages/MpProviders'
import MpServices from '@/pages/MpServices'
import OccupationalSubmissions from '@/pages/OccupationalSubmissions'
import StewardWorkbench from '@/pages/StewardWorkbench'
import StewardInstances from '@/pages/StewardInstances'
import StewardOutreach from '@/pages/StewardOutreach'
import StewardProfessional from '@/pages/StewardProfessional'
import StewardDevices from '@/pages/StewardDevices'
import StewardPhone from '@/pages/StewardPhone'
import VolunteerApplications from '@/pages/VolunteerApplications'
import { useAuthStore } from '@/stores/auth'

/** 未登录访问受保护路由时重定向 /login */
function AuthGuard({ children }: { children: ReactElement }) {
  const accessToken = useAuthStore((s) => s.accessToken)
  if (!accessToken) {
    return <Navigate to="/login" replace />
  }
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <AuthGuard>
            <AdminLayout />
          </AuthGuard>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="occupational-submissions" element={<OccupationalSubmissions />} />
        <Route path="volunteer-applications" element={<VolunteerApplications />} />
        <Route path="mp/appointments" element={<MpAppointments />} />
        <Route path="mp/consults" element={<MpConsults />} />
        <Route path="mp/providers" element={<MpProviders />} />
        <Route path="mp/drugs" element={<MpDrugs />} />
        <Route path="mp/services" element={<MpServices />} />
        <Route path="mp/commerce/packages" element={<CommercePackages />} />
        <Route path="mp/commerce/orders" element={<CommerceOrders />} />
        <Route path="mp/commerce/batches" element={<CommerceBatches />} />
        <Route path="mp/commerce/points" element={<CommercePoints />} />
        <Route path="mp/steward" element={<StewardWorkbench />} />
        <Route path="mp/steward/instances" element={<StewardInstances />} />
        <Route path="mp/steward/outreach" element={<StewardOutreach />} />
        <Route path="mp/steward/professional" element={<StewardProfessional />} />
        <Route path="mp/steward/devices" element={<StewardDevices />} />
        <Route path="mp/steward/phone" element={<StewardPhone />} />
        <Route path="audit-logs" element={<AuditLogs />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
