import type { ReactElement } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AdminLayout } from '@/components/layout/AdminLayout'
import AuditLogs from '@/pages/AuditLogs'
import Dashboard from '@/pages/Dashboard'
import Login from '@/pages/Login'
import MpAppointments from '@/pages/MpAppointments'
import MpConsults from '@/pages/MpConsults'
import MpDrugs from '@/pages/MpDrugs'
import MpProviders from '@/pages/MpProviders'
import MpServices from '@/pages/MpServices'
import OccupationalSubmissions from '@/pages/OccupationalSubmissions'
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
        <Route path="mp/appointments" element={<MpAppointments />} />
        <Route path="mp/consults" element={<MpConsults />} />
        <Route path="mp/providers" element={<MpProviders />} />
        <Route path="mp/drugs" element={<MpDrugs />} />
        <Route path="mp/services" element={<MpServices />} />
        <Route path="audit-logs" element={<AuditLogs />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
