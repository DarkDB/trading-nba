import React, { useEffect, useState } from 'react';
import { adminApi, userApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';

const ActionButton = ({ label, onClick, loading }) => (
  <Button onClick={onClick} disabled={loading} className="justify-start">
    {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
    {label}
  </Button>
);

const Metric = ({ label, value }) => (
  <div className="rounded border border-zinc-700 p-3 bg-zinc-900/40">
    <div className="text-xs text-zinc-400">{label}</div>
    <div className="text-lg font-semibold text-white">{value ?? '-'}</div>
  </div>
);

export default function OpsDashboard() {
  const [loading, setLoading] = useState({});
  const [lastResponse, setLastResponse] = useState(null);
  const [closingCapture, setClosingCapture] = useState(null);
  const [performanceLatest, setPerformanceLatest] = useState(null);
  const [gatesStatus, setGatesStatus] = useState(null);

  const runAction = async (key, fn) => {
    setLoading((s) => ({ ...s, [key]: true }));
    try {
      const res = await fn();
      const data = res?.data ?? res;
      setLastResponse(data);
      if (key === 'runDailyPaper') {
        setGatesStatus(data?.gates_status ?? null);
        if (data?.performance_latest) {
          setPerformanceLatest(data.performance_latest);
        }
      }
      if (key === 'captureClosing') {
        await fetchClosingCapture();
      }
      if (key === 'runDailyPaper' || key === 'syncUpcoming' || key === 'syncOdds') {
        await fetchPerformance();
      }
      toast.success('Accion completada');
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Error';
      setLastResponse({ error: msg });
      toast.error(msg);
    } finally {
      setLoading((s) => ({ ...s, [key]: false }));
    }
  };

  const fetchClosingCapture = async () => {
    const res = await adminApi.getClosingCaptureDiagnostics();
    setClosingCapture(res.data);
  };

  const fetchPerformance = async () => {
    const res = await adminApi.getPerformanceSummary(90);
    setPerformanceLatest(res.data?.latest || null);
  };

  useEffect(() => {
    (async () => {
      try {
        await Promise.all([fetchClosingCapture(), fetchPerformance()]);
      } catch (e) {
        // silent initial load fail
      }
    })();
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Ops Dashboard</h1>
        <p className="text-zinc-400 text-sm mt-1">Panel operativo para sync, picks, capture y run diario.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Acciones</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          <ActionButton label="Sync Upcoming (2d)" loading={!!loading.syncUpcoming} onClick={() => runAction('syncUpcoming', () => adminApi.syncUpcoming(2))} />
          <ActionButton label="Sync Odds (2d)" loading={!!loading.syncOdds} onClick={() => runAction('syncOdds', () => adminApi.syncOdds(2))} />
          <ActionButton label="Generate Picks" loading={!!loading.generatePicks} onClick={() => runAction('generatePicks', () => userApi.generatePicks())} />
          <ActionButton label="Capture Closing Lines" loading={!!loading.captureClosing} onClick={() => runAction('captureClosing', () => adminApi.captureClosingLines(30))} />
          <ActionButton label="Run Daily Paper" loading={!!loading.runDailyPaper} onClick={() => runAction('runDailyPaper', () => adminApi.runDailyPaper())} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Closing Capture</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Metric label="n_open_predictions" value={closingCapture?.n_open_predictions} />
            <Metric label="n_close_captured" value={closingCapture?.n_close_captured} />
            <Metric label="pct_with_closing_line" value={closingCapture?.pct_with_closing_line} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Performance Latest</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Metric label="n_picks_total" value={performanceLatest?.n_picks_total} />
            <Metric label="n_picks_settled" value={performanceLatest?.n_picks_settled} />
            <Metric label="roi_total" value={performanceLatest?.roi_total} />
            <Metric label="roi_50" value={performanceLatest?.roi_50} />
            <Metric label="winrate_50" value={performanceLatest?.winrate_50} />
            <Metric label="avg_p_cover_real_50" value={performanceLatest?.avg_p_cover_real_50} />
            <Metric label="brier_score_50" value={performanceLatest?.brier_score_50} />
            <Metric label="gates_status" value={gatesStatus ? JSON.stringify(gatesStatus) : '-'} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ultima respuesta</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="bg-zinc-950 border border-zinc-800 text-zinc-200 p-4 rounded text-xs overflow-auto max-h-[420px]">
            {JSON.stringify(lastResponse, null, 2)}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}
