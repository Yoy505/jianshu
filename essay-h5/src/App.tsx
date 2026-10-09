import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigationType } from 'react-router-dom';
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

type RoutePhase = 'idle' | 'leaving' | 'enter-prep' | 'entering';

function AnimatedRoutes() {
  const location = useLocation();
  const navigationType = useNavigationType();
  const [renderLocation, setRenderLocation] = useState(location);
  const [phase, setPhase] = useState<RoutePhase>('idle');
  const renderedView = useRef(location.pathname);
  const scrollMode = useRef<'top' | 'restore'>('restore');

  useLayoutEffect(() => {
    if (renderLocation.pathname === '/' || scrollMode.current === 'top') {
      window.scrollTo(0, 0);
    }
  }, [renderLocation]);

  useEffect(() => {
    const viewChanged = renderedView.current !== location.pathname;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    scrollMode.current = navigationType === 'POP' ? 'restore' : 'top';

    if (!viewChanged || navigationType === 'POP' || reducedMotion) {
      renderedView.current = location.pathname;
      setRenderLocation(location);
      setPhase('idle');
      return;
    }

    setPhase('leaving');
    let enterFrame = 0;
    let paintFrame = 0;
    let finishTimer = 0;
    const leaveTimer = window.setTimeout(() => {
      renderedView.current = location.pathname;
      setRenderLocation(location);
      setPhase('enter-prep');
      paintFrame = window.requestAnimationFrame(() => {
        enterFrame = window.requestAnimationFrame(() => setPhase('entering'));
      });
      finishTimer = window.setTimeout(() => setPhase('idle'), 496);
    }, 460);

    return () => {
      window.clearTimeout(leaveTimer);
      window.clearTimeout(finishTimer);
      window.cancelAnimationFrame(paintFrame);
      window.cancelAnimationFrame(enterFrame);
    };
  }, [location, navigationType]);

  return (
    <div className="oh-route-stage" data-phase={phase}>
      <Routes location={renderLocation}>
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
      <main className="oh-notfound">
        <div className="oh-container oh-notfound-inner">
          <p className="oh-kicker">PAGE UNAVAILABLE / 页面已下线</p>
          <h1>该页面已下线或不存在</h1>
          <p>请使用顶部导航查看征集信息，或进入投稿页面提交职业健康科普作品。</p>
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
        <AnimatedRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
