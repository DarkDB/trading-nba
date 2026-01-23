import React, { useState, useEffect } from 'react';
import { adminApi, userApi, statsApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Switch } from '../components/ui/switch';
import { Label } from '../components/ui/label';
import { toast } from 'sonner';
import { 
  Zap, 
  Loader2, 
  RefreshCw,
  Clock,
  Target,
  AlertTriangle,
  CheckCircle,
  XCircle,
  TrendingUp,
  Calendar,
  DollarSign,
  BarChart3,
  AlertOctagon,
  Lock,
  Unlock
} from 'lucide-react';

export default function LiveOps() {
  const [operativePicks, setOperativePicks] = useState([]);
  const [allPicks, setAllPicks] = useState([]);
  const [modelInfo, setModelInfo] = useState(null);
  const [auditReport, setAuditReport] = useState(null);
  const [activeCalibration, setActiveCalibration] = useState(null);
  const [showAudit, setShowAudit] = useState(false);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState({});
  const [operativeMode, setOperativeMode] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadActiveCalibration = async () => {
    try {
      const response = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/admin/calibration/current`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('nba_edge_token')}` }
      });
      const data = await response.json();
      setActiveCalibration(data);
    } catch (e) {
      console.error('Error loading calibration:', e);
    }
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const [picksRes, modelRes] = await Promise.all([
        userApi.getPicks(),
        statsApi.getModel(),
      ]);
      
      // Also load active calibration
      await loadActiveCalibration();
      
      const picks = picksRes.data.picks || [];
      setAllPicks(picks);
      setOperativePicks(picks.filter(p => !p.do_not_bet));
      setModelInfo(modelRes.data.active_model);
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadAuditReport = async () => {
    setSyncing(prev => ({ ...prev, audit: true }));
    try {
      const response = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/audit/model-sanity?n=200`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('nba_edge_token')}`
        }
      });
      const data = await response.json();
      setAuditReport(data);
      setShowAudit(true);
      if (data.flags && data.flags.length > 0) {
        toast.warning(`Audit found ${data.flags.length} flag(s)`);
      } else {
        toast.success('Audit complete - no issues found');
      }
    } catch (error) {
      toast.error('Failed to load audit report');
    } finally {
      setSyncing(prev => ({ ...prev, audit: false }));
    }
  };

  const handleAction = async (action, key) => {
    setSyncing(prev => ({ ...prev, [key]: true }));
    try {
      let response;
      switch (action) {
        case 'sync-upcoming':
          response = await adminApi.syncUpcoming(2);
          break;
        case 'sync-odds':
          response = await adminApi.syncOdds(2);
          break;
        case 'generate-picks':
          response = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/picks/generate?operative_mode=${operativeMode}`, {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${localStorage.getItem('nba_edge_token')}`,
              'Content-Type': 'application/json'
            }
          });
          response = await response.json();
          if (response.picks) {
            setAllPicks(response.all_picks || response.picks);
            setOperativePicks(operativeMode ? response.picks : response.picks.filter(p => !p.do_not_bet));
            toast.success(`Generated ${response.count} picks (${response.total_analyzed || response.count} analyzed)`);
            return;
          }
          break;
        case 'snapshot-close':
          response = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/admin/snapshot-close-lines?minutes_before=60`, {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${localStorage.getItem('nba_edge_token')}`
            }
          });
          response = await response.json();
          break;
        default:
          return;
      }
      toast.success(response.data?.message || response.message || 'Action completed');
      loadData();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Action failed');
    } finally {
      setSyncing(prev => ({ ...prev, [key]: false }));
    }
  };

  const getSignalBadge = (signal) => {
    const classes = {
      green: 'bg-green-500/10 text-green-500 border-green-500/30',
      yellow: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/30',
      red: 'bg-red-500/10 text-red-500 border-red-500/30'
    };
    return classes[signal] || classes.red;
  };

  // Separate today and tomorrow picks
  const now = new Date();
  const todayStr = now.toISOString().split('T')[0];
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowStr = tomorrow.toISOString().split('T')[0];

  const todayPicks = operativePicks.filter(p => p.commence_time?.startsWith(todayStr));
  const tomorrowPicks = operativePicks.filter(p => p.commence_time?.startsWith(tomorrowStr));

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="live-ops-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
            Live Ops
          </h1>
          <p className="text-zinc-400 mt-1">Operativa de apuestas en tiempo real</p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Switch 
              id="operative-mode" 
              checked={operativeMode}
              onCheckedChange={setOperativeMode}
            />
            <Label htmlFor="operative-mode" className="text-zinc-400 text-sm">
              Modo Operativo
            </Label>
          </div>
        </div>
      </div>

      {/* Model Health Card */}
      {modelInfo && (
        <Card className="bg-card border-border">
          <CardHeader className="pb-2">
            <CardTitle className="font-headings text-lg text-white flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-primary" />
              Model Health
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div>
                <p className="text-xs text-zinc-500 uppercase">Version</p>
                <p className="font-data text-white">{modelInfo.model_version}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase">MAE</p>
                <p className="font-data text-white">{modelInfo.metrics?.mae?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase">RMSE</p>
                <p className="font-data text-white">{modelInfo.metrics?.rmse?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase">Pred Std (Test)</p>
                <p className="font-data text-white">{modelInfo.metrics?.pred_std_test?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase">Data Cutoff</p>
                <p className="font-data text-white">{modelInfo.data_cutoff_date}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Action Buttons */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-lg text-white">
            Runbook Operativo
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Ejecuta estos pasos en orden para operar hoy
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Button
              onClick={() => handleAction('sync-upcoming', 'upcoming')}
              disabled={syncing.upcoming}
              className="h-auto py-4 flex flex-col gap-2 bg-zinc-800 hover:bg-zinc-700"
              data-testid="btn-sync-upcoming"
            >
              {syncing.upcoming ? <Loader2 className="w-5 h-5 animate-spin" /> : <Calendar className="w-5 h-5" />}
              <span className="text-xs">1. Sync Upcoming</span>
            </Button>
            
            <Button
              onClick={() => handleAction('sync-odds', 'odds')}
              disabled={syncing.odds}
              className="h-auto py-4 flex flex-col gap-2 bg-zinc-800 hover:bg-zinc-700"
              data-testid="btn-sync-odds"
            >
              {syncing.odds ? <Loader2 className="w-5 h-5 animate-spin" /> : <DollarSign className="w-5 h-5" />}
              <span className="text-xs">2. Sync Odds</span>
            </Button>
            
            <Button
              onClick={() => handleAction('generate-picks', 'picks')}
              disabled={syncing.picks}
              className="h-auto py-4 flex flex-col gap-2 bg-primary hover:bg-primary/90"
              data-testid="btn-generate-picks"
            >
              {syncing.picks ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-5 h-5" />}
              <span className="text-xs">3. Generate Picks</span>
            </Button>
            
            <Button
              onClick={() => handleAction('snapshot-close', 'close')}
              disabled={syncing.close}
              className="h-auto py-4 flex flex-col gap-2 bg-zinc-800 hover:bg-zinc-700"
              data-testid="btn-snapshot-close"
            >
              {syncing.close ? <Loader2 className="w-5 h-5 animate-spin" /> : <Clock className="w-5 h-5" />}
              <span className="text-xs">4. Snapshot Close (T-60)</span>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ACTIVE CALIBRATION BLOCK - Always visible */}
      <Card className={`border-2 ${activeCalibration?.is_auditable ? 'bg-zinc-900/50 border-green-500/30' : 'bg-red-900/20 border-red-500/50'}`}>
        <CardHeader className="py-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              {activeCalibration?.is_auditable ? (
                <CheckCircle className="w-4 h-4 text-green-400" />
              ) : (
                <AlertOctagon className="w-4 h-4 text-red-400" />
              )}
              Calibration (Active)
            </CardTitle>
            {activeCalibration?.is_locked ? (
              <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-500/50">
                <Lock className="w-3 h-3 mr-1" /> LOCKED
              </Badge>
            ) : activeCalibration?.is_auditable ? (
              <Badge className="bg-green-500/20 text-green-400 border-green-500/50">
                <Unlock className="w-3 h-3 mr-1" /> ACTIVE
              </Badge>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="py-2">
          {activeCalibration?.error ? (
            <div className="p-3 bg-red-500/10 rounded border border-red-500/30">
              <p className="text-red-400 font-bold text-sm">⚠️ CALIBRATION NOT AUDITABLE</p>
              <p className="text-red-300 text-xs mt-1">{activeCalibration.message || activeCalibration.error}</p>
            </div>
          ) : activeCalibration?.is_auditable ? (
            <div className="space-y-3">
              {/* Main calibration values */}
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 text-xs">
                <div className="bg-zinc-800/50 p-2 rounded">
                  <span className="text-zinc-500 block">calibration_id</span>
                  <span className="text-green-400 font-mono text-[10px]">{activeCalibration.calibration_id}</span>
                </div>
                <div className="bg-zinc-800/50 p-2 rounded">
                  <span className="text-zinc-500 block">probability_mode</span>
                  <span className="text-green-400 font-bold">{activeCalibration.probability_mode}</span>
                </div>
                <div className="bg-green-900/30 p-2 rounded border border-green-500/30">
                  <span className="text-zinc-500 block">β_effective</span>
                  <span className="text-green-400 font-mono font-bold text-sm">{activeCalibration.beta?.toFixed(4)}</span>
                </div>
                <div className="bg-green-900/30 p-2 rounded border border-green-500/30">
                  <span className="text-zinc-500 block">α_effective</span>
                  <span className="text-green-400 font-mono font-bold text-sm">{activeCalibration.alpha?.toFixed(4)}</span>
                </div>
                <div className="bg-green-900/30 p-2 rounded border border-green-500/30">
                  <span className="text-zinc-500 block">σ_residual</span>
                  <span className="text-green-400 font-mono font-bold text-sm">{activeCalibration.sigma_residual?.toFixed(2)}</span>
                </div>
                <div className="bg-zinc-800/50 p-2 rounded">
                  <span className="text-zinc-500 block">n_spread</span>
                  <span className="text-white font-mono">{activeCalibration.n_spread_samples}</span>
                </div>
              </div>
              
              {/* Shrinkage details */}
              <div className="p-2 bg-blue-900/20 rounded border border-blue-500/20">
                <p className="text-blue-400 text-xs font-bold mb-2">Shrinkage Details</p>
                <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs">
                  <div>
                    <span className="text-zinc-500 block">β_reg</span>
                    <span className="text-zinc-300 font-mono">{activeCalibration.beta_reg?.toFixed(4)}</span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">β_prior</span>
                    <span className="text-zinc-300 font-mono">{activeCalibration.beta_prior?.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">α_reg</span>
                    <span className="text-zinc-300 font-mono">{activeCalibration.alpha_reg?.toFixed(4)}</span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">α_prior</span>
                    <span className="text-zinc-300 font-mono">{activeCalibration.alpha_prior?.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">k</span>
                    <span className="text-zinc-300 font-mono">{activeCalibration.k_shrinkage}</span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">w</span>
                    <span className="text-zinc-300 font-mono">{activeCalibration.w_shrinkage?.toFixed(4)}</span>
                    {activeCalibration.beta_clamped && (
                      <Badge className="ml-1 text-[8px] bg-yellow-500/20 text-yellow-400">CLAMPED</Badge>
                    )}
                  </div>
                </div>
              </div>
              
              {/* Metadata */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-zinc-500">
                <div>
                  <span className="block">computed_at</span>
                  <span className="text-zinc-400 font-mono text-[10px]">{activeCalibration.computed_at?.slice(0, 19)}</span>
                </div>
                <div>
                  <span className="block">data_cutoff</span>
                  <span className="text-zinc-400 font-mono">{activeCalibration.data_cutoff}</span>
                </div>
                <div>
                  <span className="block">model_version</span>
                  <span className="text-zinc-400 font-mono text-[10px]">{activeCalibration.model_version}</span>
                </div>
                <div>
                  <span className="block">beta_source</span>
                  <span className="text-zinc-400 font-mono text-[10px]">{activeCalibration.beta_source}</span>
                </div>
                <span className="text-zinc-500 block">data_cutoff</span>
                <span className="text-zinc-300 font-mono">{activeCalibration.data_cutoff}</span>
              </div>
              <div className="bg-zinc-800/50 p-2 rounded">
                <span className="text-zinc-500 block">model_version</span>
                <span className="text-zinc-300 font-mono text-[10px]">{activeCalibration.model_version}</span>
              </div>
            </div>
          ) : (
            <div className="p-3 bg-red-500/10 rounded border border-red-500/30">
              <p className="text-red-400 font-bold text-sm">⚠️ CALIBRATION NOT AUDITABLE</p>
              <p className="text-red-300 text-xs mt-1">Run POST /api/admin/model/calibrate-vs-market to create calibration</p>
            </div>
          )}
          
          {/* Action buttons */}
          <div className="flex gap-2 mt-3">
            <Button
              onClick={async () => {
                setSyncing(prev => ({ ...prev, sigma: true }));
                try {
                  const response = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/admin/model/calibrate-vs-market`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${localStorage.getItem('nba_edge_token')}` }
                  });
                  const data = await response.json();
                  if (data.status === 'completed') {
                    toast.success(`New calibration: ${data.calibration_id}`);
                    await loadActiveCalibration();
                    loadAuditReport();
                  } else {
                    toast.error(data.warning || data.error || 'Error en calibración');
                  }
                } catch (e) {
                  toast.error('Error calibrating VS_MARKET');
                } finally {
                  setSyncing(prev => ({ ...prev, sigma: false }));
                }
              }}
              disabled={syncing.sigma || activeCalibration?.is_locked}
              variant="outline"
              size="sm"
              className="border-blue-500/50 text-blue-400 hover:bg-blue-500/10"
            >
              {syncing.sigma ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <RefreshCw className="w-4 h-4 mr-2" />}
              Recalibrate
            </Button>
            <Button
              onClick={loadAuditReport}
              disabled={syncing.audit}
              variant="outline"
              size="sm"
              className="border-yellow-500/50 text-yellow-400 hover:bg-yellow-500/10"
            >
              {syncing.audit ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <BarChart3 className="w-4 h-4 mr-2" />}
              Model Audit
            </Button>
            <Button
              onClick={loadActiveCalibration}
              variant="outline"
              size="sm"
              className="border-zinc-500/50 text-zinc-400 hover:bg-zinc-500/10"
            >
              <RefreshCw className="w-4 h-4 mr-2" />
              Refresh
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Operative Filters Info */}
      {operativeMode && (
        <Card className="bg-zinc-900/50 border-border">
          <CardContent className="py-3">
            <div className="flex items-center gap-4 text-sm flex-wrap">
              <span className="text-zinc-500">Filtros Operativos:</span>
              <Badge variant="outline" className="text-xs bg-green-500/10 text-green-400 border-green-500/30">EV ≥ 2%</Badge>
              <Badge variant="outline" className="text-xs">HIGH confidence</Badge>
              <Badge variant="outline" className="text-xs">Pinnacle required</Badge>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Model Audit Report */}
      {showAudit && auditReport && (
        <Card className="bg-card border-border border-yellow-500/30">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
                <BarChart3 className="w-5 h-5 text-yellow-500" />
                Model Sanity Audit
              </CardTitle>
              <Button variant="ghost" size="sm" onClick={() => setShowAudit(false)} className="text-zinc-400">
                <XCircle className="w-4 h-4" />
              </Button>
            </div>
            <CardDescription className="text-zinc-400">
              Análisis de {auditReport.statistics?.n_samples || 0} predicciones
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Flags */}
            {auditReport.flags && auditReport.flags.length > 0 && (
              <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
                <div className="flex items-center gap-2 mb-2">
                  <AlertOctagon className="w-5 h-5 text-red-500" />
                  <span className="font-bold text-red-400">FLAGS DETECTADOS ({auditReport.flags.length})</span>
                </div>
                <ul className="space-y-1">
                  {auditReport.flags.map((flag, i) => (
                    <li key={i} className="text-red-300 text-sm font-mono">• {flag}</li>
                  ))}
                </ul>
              </div>
            )}
            
            {auditReport.flags?.length === 0 && (
              <div className="p-4 bg-green-500/10 border border-green-500/30 rounded-lg">
                <div className="flex items-center gap-2">
                  <CheckCircle className="w-5 h-5 text-green-500" />
                  <span className="font-bold text-green-400">NO FLAGS - Model appears healthy</span>
                </div>
              </div>
            )}

            {/* Statistics Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-3 bg-zinc-900/50 rounded-lg">
                <p className="text-xs text-zinc-500 uppercase">pred_margin mean</p>
                <p className="font-data text-xl text-white">{auditReport.statistics?.pred_margin?.mean?.toFixed(2)}</p>
              </div>
              <div className="p-3 bg-zinc-900/50 rounded-lg">
                <p className="text-xs text-zinc-500 uppercase">pred_margin std</p>
                <p className="font-data text-xl text-white">{auditReport.statistics?.pred_margin?.std?.toFixed(2)}</p>
              </div>
              <div className="p-3 bg-zinc-900/50 rounded-lg">
                <p className="text-xs text-zinc-500 uppercase">mean |pred_margin|</p>
                <p className="font-data text-xl text-white">{auditReport.statistics?.pred_margin?.mean_abs?.toFixed(2)}</p>
              </div>
              <div className="p-3 bg-zinc-900/50 rounded-lg">
                <p className="text-xs text-zinc-500 uppercase">pred_margin range</p>
                <p className="font-data text-lg text-white">{auditReport.statistics?.pred_margin?.min?.toFixed(1)} to {auditReport.statistics?.pred_margin?.max?.toFixed(1)}</p>
              </div>
            </div>

            {/* Distribution Stats */}
            <div>
              <h4 className="text-sm font-bold text-zinc-400 mb-3 uppercase">Distribución</h4>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
                <div className="p-2 bg-zinc-900/50 rounded text-center">
                  <p className="text-xs text-zinc-500">|pred| &gt; 10</p>
                  <p className={`font-data font-bold ${auditReport.statistics?.distributions?.pct_abs_pred_margin_gt_10 > 30 ? 'text-yellow-400' : 'text-white'}`}>
                    {auditReport.statistics?.distributions?.pct_abs_pred_margin_gt_10}%
                  </p>
                </div>
                <div className="p-2 bg-zinc-900/50 rounded text-center">
                  <p className="text-xs text-zinc-500">|pred| &gt; 15</p>
                  <p className={`font-data font-bold ${auditReport.statistics?.distributions?.pct_abs_pred_margin_gt_15 > 25 ? 'text-red-400' : 'text-white'}`}>
                    {auditReport.statistics?.distributions?.pct_abs_pred_margin_gt_15}%
                  </p>
                </div>
                <div className="p-2 bg-zinc-900/50 rounded text-center">
                  <p className="text-xs text-zinc-500">|pred| &gt; 20</p>
                  <p className={`font-data font-bold ${auditReport.statistics?.distributions?.pct_abs_pred_margin_gt_20 > 15 ? 'text-red-400' : 'text-white'}`}>
                    {auditReport.statistics?.distributions?.pct_abs_pred_margin_gt_20}%
                  </p>
                </div>
                <div className="p-2 bg-zinc-900/50 rounded text-center">
                  <p className="text-xs text-zinc-500">edge ≥ 8</p>
                  <p className="font-data font-bold text-white">{auditReport.statistics?.distributions?.pct_betting_edge_gte_8}%</p>
                </div>
                <div className="p-2 bg-zinc-900/50 rounded text-center">
                  <p className="text-xs text-zinc-500">edge ≥ 10</p>
                  <p className="font-data font-bold text-white">{auditReport.statistics?.distributions?.pct_betting_edge_gte_10}%</p>
                </div>
                <div className="p-2 bg-zinc-900/50 rounded text-center">
                  <p className="text-xs text-zinc-500">edge ≥ 12</p>
                  <p className="font-data font-bold text-white">{auditReport.statistics?.distributions?.pct_betting_edge_gte_12}%</p>
                </div>
              </div>
            </div>

            {/* Top 10 Extreme Picks */}
            <div>
              <h4 className="text-sm font-bold text-zinc-400 mb-3 uppercase">Top 10 Picks Más Extremos (por betting_edge)</h4>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-zinc-700">
                      <th className="text-left p-2 text-xs text-zinc-500">Teams</th>
                      <th className="text-right p-2 text-xs text-zinc-500">pred_margin</th>
                      <th className="text-right p-2 text-xs text-zinc-500">spread</th>
                      <th className="text-right p-2 text-xs text-zinc-500">threshold</th>
                      <th className="text-right p-2 text-xs text-zinc-500">raw_edge</th>
                      <th className="text-right p-2 text-xs text-zinc-500">betting_edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditReport.top_10_extreme_picks?.map((pick, i) => (
                      <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                        <td className="p-2 text-white">{pick.home_team?.slice(0, 15)} vs {pick.away_team?.slice(0, 15)}</td>
                        <td className={`p-2 text-right font-data ${Math.abs(pick.pred_margin) > 15 ? 'text-red-400' : 'text-white'}`}>
                          {pick.pred_margin > 0 ? '+' : ''}{pick.pred_margin?.toFixed(2)}
                        </td>
                        <td className="p-2 text-right font-data text-blue-400">{pick.market_spread?.toFixed(1)}</td>
                        <td className="p-2 text-right font-data text-zinc-400">{pick.cover_threshold?.toFixed(1)}</td>
                        <td className="p-2 text-right font-data text-yellow-400">{pick.raw_edge_signed > 0 ? '+' : ''}{pick.raw_edge_signed?.toFixed(2)}</td>
                        <td className={`p-2 text-right font-data font-bold ${pick.betting_edge > 10 ? 'text-red-400' : 'text-green-400'}`}>
                          {pick.betting_edge?.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Model Info */}
            <div>
              <h4 className="text-sm font-bold text-zinc-400 mb-3 uppercase">Model & Calibration Info</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <div>
                  <span className="text-zinc-500">calibration_id:</span>
                  <span className="text-green-400 ml-2 font-mono text-xs">{auditReport.calibration_id}</span>
                </div>
                <div>
                  <span className="text-zinc-500">β (beta):</span>
                  <span className="text-blue-400 ml-2 font-data font-bold">{auditReport.model_info?.calibration?.beta_used?.toFixed(4)}</span>
                </div>
                <div>
                  <span className="text-zinc-500">σ (sigma):</span>
                  <span className="text-blue-400 ml-2 font-data font-bold">{auditReport.model_info?.calibration?.sigma_used?.toFixed(2)}</span>
                </div>
                <div>
                  <span className="text-zinc-500">alpha:</span>
                  <span className="text-white ml-2 font-data">{auditReport.model_info?.calibration?.alpha_used?.toFixed(4)}</span>
                </div>
                <div>
                  <span className="text-zinc-500">Model Version:</span>
                  <span className="text-white ml-2 font-data text-xs">{auditReport.model_info?.model_version}</span>
                </div>
                <div>
                  <span className="text-zinc-500">beta_source:</span>
                  <span className="text-zinc-300 ml-2 font-mono text-xs">{auditReport.model_info?.calibration?.beta_source}</span>
                </div>
                <div>
                  <span className="text-zinc-500">MAE:</span>
                  <span className="text-white ml-2 font-data">{auditReport.model_info?.mae?.toFixed(2)}</span>
                </div>
                <div>
                  <span className="text-zinc-500">RMSE:</span>
                  <span className="text-white ml-2 font-data">{auditReport.model_info?.rmse?.toFixed(2)}</span>
                </div>
              </div>
              <div className="mt-3 p-3 bg-zinc-900/50 rounded">
                <p className="text-xs text-zinc-500 mb-2">Coefficients:</p>
                <div className="flex flex-wrap gap-2">
                  {auditReport.model_info?.coefficients && Object.entries(auditReport.model_info.coefficients).map(([k, v]) => (
                    <Badge key={k} variant="outline" className="text-xs font-data">
                      {k}: {v?.toFixed(3)}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* All Valid Picks - Main Section */}
      <Card className="bg-card border-border border-primary/50">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-primary" />
            All Valid Picks ({operativePicks.length})
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Picks con EV ≥ 2% + HIGH confidence + Pinnacle. Ordenados por EV descendente.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {operativePicks.length === 0 ? (
            <div className="text-center py-8 text-zinc-500">
              No hay picks válidos. Ejecuta Sync Upcoming → Sync Odds → Generate Picks
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-700">
                    <th className="text-left text-xs text-zinc-400 p-2 uppercase tracking-wider">Partido</th>
                    <th className="text-center text-xs text-zinc-400 p-2 uppercase tracking-wider">Apuesta</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider">Price</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider">Impl%</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider">p_cover</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider font-bold text-green-400">EV%</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider">Pred</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider">Thresh</th>
                    <th className="text-right text-xs text-zinc-400 p-2 uppercase tracking-wider">σ</th>
                  </tr>
                </thead>
                <tbody>
                  {operativePicks.map((pick, idx) => {
                    const spread = pick.open_spread || 0;
                    const coverThreshold = pick.cover_threshold ?? -spread;
                    const pCover = pick.p_cover ?? 0.5;
                    const impliedProb = pick.implied_prob ?? (1 / (pick.open_price || 1.91));
                    const ev = pick.ev ?? 0;
                    const sigmaUsed = pick.sigma_used ?? pick.sigma_residual ?? '?';
                    const betaUsed = pick.beta_used ?? '?';
                    
                    return (
                      <tr key={pick.id} className={`border-b border-zinc-800/50 hover:bg-zinc-800/30 ${idx === 0 ? 'bg-green-500/10' : ''}`}>
                        <td className="p-2">
                          <div className="flex flex-col">
                            <span className="text-white font-medium text-xs">{pick.home_team?.slice(0, 18)}</span>
                            <span className="text-zinc-500 text-xs">vs {pick.away_team?.slice(0, 18)}</span>
                          </div>
                        </td>
                        <td className="p-2 text-center">
                          <Badge className="bg-primary/20 text-primary border-primary/50 font-data font-bold text-xs px-2">
                            {pick.recommended_bet_string}
                          </Badge>
                        </td>
                        <td className="p-2 text-right font-data text-white">
                          {pick.open_price?.toFixed(2)}
                        </td>
                        <td className="p-2 text-right font-data text-zinc-400">
                          {(impliedProb * 100).toFixed(1)}%
                        </td>
                        <td className={`p-2 text-right font-data ${pCover > impliedProb ? 'text-green-400' : 'text-zinc-400'}`}>
                          {(pCover * 100).toFixed(1)}%
                        </td>
                        <td className={`p-2 text-right font-data font-bold ${ev >= 0.05 ? 'text-green-400' : ev >= 0.02 ? 'text-yellow-400' : 'text-red-400'}`}>
                          {ev >= 0 ? '+' : ''}{(ev * 100).toFixed(1)}%
                        </td>
                        <td className={`p-2 text-right font-data ${Math.abs(pick.pred_margin) > 15 ? 'text-red-400' : 'text-white'}`}>
                          {pick.pred_margin > 0 ? '+' : ''}{pick.pred_margin?.toFixed(1)}
                        </td>
                        <td className="p-2 text-right font-data text-zinc-500">
                          {coverThreshold > 0 ? '+' : ''}{coverThreshold?.toFixed(1)}
                        </td>
                        <td className="p-2 text-right font-data text-blue-400" title={`β=${betaUsed}`}>
                          {typeof sigmaUsed === 'number' ? sigmaUsed.toFixed(1) : sigmaUsed}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Today's Picks */}
      <Card className="bg-card border-border border-green-500/30">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
            <Target className="w-5 h-5 text-green-500" />
            Today's Picks ({todayPicks.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {todayPicks.length === 0 ? (
            <div className="text-center py-8 text-zinc-500">
              No hay picks operativos para hoy
            </div>
          ) : (
            <div className="space-y-4">
              {todayPicks.map((pick) => (
                <PickCard key={pick.id} pick={pick} getSignalBadge={getSignalBadge} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Tomorrow's Picks */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
            <Calendar className="w-5 h-5 text-secondary" />
            Tomorrow's Picks ({tomorrowPicks.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {tomorrowPicks.length === 0 ? (
            <div className="text-center py-8 text-zinc-500">
              No hay picks operativos para mañana
            </div>
          ) : (
            <div className="space-y-4">
              {tomorrowPicks.map((pick) => (
                <PickCard key={pick.id} pick={pick} getSignalBadge={getSignalBadge} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* All Analyzed Picks (if not in operative mode) */}
      {!operativeMode && allPicks.length > 0 && (
        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="font-headings text-xl text-white">
              All Analyzed Picks ({allPicks.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    <th className="text-left text-xs text-zinc-500 p-2">Partido</th>
                    <th className="text-left text-xs text-zinc-500 p-2">Hora</th>
                    <th className="text-right text-xs text-zinc-500 p-2">Pred</th>
                    <th className="text-right text-xs text-zinc-500 p-2">Spread</th>
                    <th className="text-right text-xs text-zinc-500 p-2">Edge</th>
                    <th className="text-center text-xs text-zinc-500 p-2">Signal</th>
                    <th className="text-center text-xs text-zinc-500 p-2">Bet?</th>
                    <th className="text-left text-xs text-zinc-500 p-2">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {allPicks.map((pick) => (
                    <tr key={pick.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                      <td className="p-2">
                        <span className="text-white text-sm">{pick.home_abbr || pick.home_team?.slice(0,3)}</span>
                        <span className="text-zinc-500 mx-1">vs</span>
                        <span className="text-zinc-300 text-sm">{pick.away_abbr || pick.away_team?.slice(0,3)}</span>
                      </td>
                      <td className="p-2 text-sm text-zinc-400">{pick.commence_time_local}</td>
                      <td className="p-2 text-right font-data text-white">{pick.pred_margin > 0 ? '+' : ''}{pick.pred_margin?.toFixed(1)}</td>
                      <td className="p-2 text-right font-data text-blue-400">{pick.open_spread > 0 ? '+' : ''}{pick.open_spread?.toFixed(1)}</td>
                      <td className="p-2 text-right font-data font-bold" style={{color: pick.signal === 'green' ? '#22c55e' : pick.signal === 'yellow' ? '#eab308' : '#ef4444'}}>
                        {pick.edge_points > 0 ? '+' : ''}{pick.edge_points?.toFixed(1)}
                      </td>
                      <td className="p-2 text-center">
                        <Badge className={`${getSignalBadge(pick.signal)} border text-xs`}>
                          {pick.signal?.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="p-2 text-center">
                        {pick.do_not_bet ? (
                          <XCircle className="w-4 h-4 text-red-500 mx-auto" />
                        ) : (
                          <CheckCircle className="w-4 h-4 text-green-500 mx-auto" />
                        )}
                      </td>
                      <td className="p-2 text-xs text-zinc-500">{pick.do_not_bet_reason || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// Pick Card Component
function PickCard({ pick, getSignalBadge }) {
  return (
    <div className="p-4 bg-zinc-900/50 rounded-lg border border-zinc-800">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="font-headings font-bold text-lg text-white">
              {pick.home_team}
            </span>
            <span className="text-zinc-500">vs</span>
            <span className="font-headings font-bold text-lg text-zinc-300">
              {pick.away_team}
            </span>
          </div>
          <div className="flex items-center gap-3 text-sm text-zinc-400">
            <span className="flex items-center gap-1">
              <Clock className="w-4 h-4" />
              {pick.commence_time_local}
            </span>
            <Badge className={`${getSignalBadge(pick.signal)} border`}>
              {pick.signal?.toUpperCase()}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {pick.confidence?.toUpperCase()}
            </Badge>
          </div>
        </div>
        <div className="text-right">
          <p className="text-xs text-zinc-500 uppercase">Edge</p>
          <p className={`font-data text-2xl font-bold ${
            pick.edge_points > 0 ? 'text-green-500' : 'text-red-500'
          }`}>
            {pick.edge_points > 0 ? '+' : ''}{pick.edge_points?.toFixed(1)}
          </p>
        </div>
      </div>
      
      {/* Recommended Bet - THE MOST IMPORTANT PART */}
      <div className="p-3 bg-green-500/10 border border-green-500/30 rounded-md mb-3">
        <p className="text-xs text-green-400 uppercase tracking-wider mb-1">Apuesta Recomendada</p>
        <p className="font-headings font-bold text-xl text-green-400" data-testid="recommended-bet">
          {pick.recommended_bet_string}
        </p>
      </div>
      
      {/* Details Grid */}
      <div className="grid grid-cols-4 gap-4 mb-3">
        <div>
          <p className="text-xs text-zinc-500">Pred. Margin</p>
          <p className="font-data text-white">{pick.pred_margin > 0 ? '+' : ''}{pick.pred_margin?.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-zinc-500">Open Spread</p>
          <p className="font-data text-blue-400">{pick.open_spread > 0 ? '+' : ''}{pick.open_spread?.toFixed(1)}</p>
        </div>
        <div>
          <p className="text-xs text-zinc-500">Open Price</p>
          <p className="font-data text-blue-400">{pick.open_price?.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-zinc-500">Book</p>
          <p className="font-data text-white">{pick.reference_bookmaker_used}</p>
        </div>
      </div>
      
      {/* CLV if available */}
      {pick.close_spread !== null && pick.close_spread !== undefined && (
        <div className="grid grid-cols-3 gap-4 mb-3 p-2 bg-zinc-800/50 rounded">
          <div>
            <p className="text-xs text-zinc-500">Close Spread</p>
            <p className="font-data text-white">{pick.close_spread > 0 ? '+' : ''}{pick.close_spread?.toFixed(1)}</p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">Close Price</p>
            <p className="font-data text-white">{pick.close_price?.toFixed(2)}</p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">CLV</p>
            <p className={`font-data font-bold ${(pick.clv_spread || 0) > 0 ? 'text-green-500' : 'text-red-500'}`}>
              {pick.clv_spread > 0 ? '+' : ''}{pick.clv_spread?.toFixed(2)}
            </p>
          </div>
        </div>
      )}
      
      {/* Explanation */}
      <div className="p-2 bg-zinc-800/30 rounded text-xs text-zinc-400 font-mono">
        {pick.explanation}
      </div>
    </div>
  );
}
