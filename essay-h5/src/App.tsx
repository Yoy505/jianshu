import { useEffect, useLayoutEffect, useRef } from 'react';
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigationType } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import axios from 'axios';
import OccupationalHomePage from './pages/OccupationalHomePage';
import { DirectionsPage, ProcessPage, RequirementsPage } from './pages/OccupationalContentPages';
import OccupationalSubmitPage from './pages/OccupationalSubmitPage';
import OccupationalReceiptPage from './pages/OccupationalReceiptPage';
import { OccupationalFooter, OccupationalHeader } from './components/OccupationalShell';
import './occupational.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (axios.isAxiosError(error)) {
          const status = error.response?.status;
          if (status !== undefined && status >= 400 && status < 500) return false;
        }
        return failureCount < 1;
      },
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

function PageRoutes() {
  const location = useLocation();
  const navigationType = useNavigationType();
  const renderedView = useRef(location.pathname);

  useLayoutEffect(() => {
    if (renderedView.current !== location.pathname) {
      document.getElementById('main-content')?.focus({ preventScroll: true });
    }
    if (navigationType !== 'POP') {
      window.scrollTo(0, 0);
    }
    renderedView.current = location.pathname;
  }, [location.pathname, navigationType]);

  return (
    <div className="oh-route-stage">
      <Routes>
        <Route path="/" element={<LegacyHomeRoute />} />
        <Route path="/directions" element={<DirectionsPage />} />
        <Route path="/requirements" element={<RequirementsPage />} />
        <Route path="/process" element={<ProcessPage />} />
        <Route path="/submit" element={<OccupationalSubmitPage />} />
        <Route path="/receipt" element={<OccupationalReceiptPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </div>
  );
}

function LegacyHomeRoute() {
  const { hash } = useLocation();
  const legacyRoutes: Record<string, string> = {
    '#directions': '/directions',
    '#requirements': '/requirements',
    '#process': '/process',
  };
  return legacyRoutes[hash] ? <Navigate to={legacyRoutes[hash]} replace /> : <OccupationalHomePage />;
}

function NotFoundPage() {
  useEffect(() => {
    document.title = '页面已下线 · 2026 职业健康科普作品征集';
  }, []);

  return (
    <div className="oh-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1} className="oh-notfound">
        <div className="oh-container oh-notfound-inner">
          <p className="oh-kicker">PAGE UNAVAILABLE / 页面已下线</p>
          <h1>该页面已下线或不存在</h1>
          <p>请使用顶部导航查看征集信息，或进入投稿页面提交职业健康科普作品。</p>
          <Link className="oh-button oh-button-primary" to="/">返回首页</Link>
        </div>
      </main>
      <OccupationalFooter />
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PageRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
