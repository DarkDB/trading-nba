import React, { useState, useEffect } from 'react';
import { userApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import { toast } from 'sonner';
import { 
  History as HistoryIcon, 
  Loader2, 
  Download,
  CheckCircle,
  XCircle,
  TrendingUp
} from 'lucide-react';
import { formatDate, formatSpread, getSignalBadgeClass, downloadBlob } from '../lib/utils';

export default function History() {
  const [predictions, setPredictions] = useState([]);
  const [stats, setStats] = useState(null);
  const [bySignal, setBySignal] = useState({});
  const [loading, setLoading] = useState(true);
  const [signalFilter, setSignalFilter] = useState('all');
  const [coveredFilter, setCoveredFilter] = useState('all');
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    loadHistory();
  }, [signalFilter, coveredFilter]);

  const loadHistory = async () => {
    try {
      const params = {};
      if (signalFilter !== 'all') params.signal = signalFilter;
      if (coveredFilter !== 'all') params.covered = coveredFilter === 'true';
      
      const response = await userApi.getHistory(params);
      setPredictions(response.data.predictions || []);
      setStats(response.data.stats);
      setBySignal(response.data.by_signal || {});
    } catch (error) {
      console.error('Error loading history:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const response = await userApi.exportHistory();
      downloadBlob(response.data, `nba_edge_history_${new Date().toISOString().split('T')[0]}.csv`);
      toast.success('Historial exportado correctamente');
    } catch (error) {
      toast.error('Error al exportar');
    } finally {
      setExporting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="history-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
            History
          </h1>
          <p className="text-zinc-400 mt-1">Rendimiento histórico de predicciones</p>
        </div>
        <Button
          onClick={handleExport}
          disabled={exporting}
          variant="outline"
          className="border-zinc-700 text-zinc-300 hover:bg-zinc-800"
          data-testid="export-csv-btn"
        >
          {exporting ? (
            <Loader2 className="w-4 h-4 animate-spin mr-2" />
          ) : (
            <Download className="w-4 h-4 mr-2" />
          )}
          Export CSV
        </Button>
      </div>

      {/* Stats Summary */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card className="bg-card border-border">
            <CardContent className="py-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-md bg-primary/10">
                  <TrendingUp className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <p className="font-data text-2xl font-bold text-white">
                    {stats.hit_rate?.toFixed(1)}%
                  </p>
                  <p className="text-xs text-zinc-500 uppercase tracking-wider">Hit Rate</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardContent className="py-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-md bg-green-500/10">
                  <CheckCircle className="w-5 h-5 text-green-500" />
                </div>
                <div>
                  <p className="font-data text-2xl font-bold text-white">
                    {stats.covered}
                  </p>
                  <p className="text-xs text-zinc-500 uppercase tracking-wider">Covered</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardContent className="py-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-md bg-red-500/10">
                  <XCircle className="w-5 h-5 text-red-500" />
                </div>
                <div>
                  <p className="font-data text-2xl font-bold text-white">
                    {stats.total - stats.covered}
                  </p>
                  <p className="text-xs text-zinc-500 uppercase tracking-wider">Not Covered</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardContent className="py-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-md bg-secondary/10">
                  <HistoryIcon className="w-5 h-5 text-secondary" />
                </div>
                <div>
                  <p className="font-data text-2xl font-bold text-white">
                    {stats.total}
                  </p>
                  <p className="text-xs text-zinc-500 uppercase tracking-wider">Total</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Performance by Signal */}
      {Object.keys(bySignal).length > 0 && (
        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="font-headings text-lg text-white">
              Rendimiento por Señal
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4">
              {['green', 'yellow', 'red'].map((signal) => {
                const data = bySignal[signal] || { total: 0, covered: 0 };
                const hitRate = data.total > 0 ? (data.covered / data.total * 100) : 0;
                
                return (
                  <div 
                    key={signal}
                    className={`p-4 rounded-md border ${
                      signal === 'green' ? 'border-green-500/20 bg-green-500/5' :
                      signal === 'yellow' ? 'border-yellow-500/20 bg-yellow-500/5' :
                      'border-red-500/20 bg-red-500/5'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-3">
                      <Badge className={`${getSignalBadgeClass(signal)} border`}>
                        {signal.toUpperCase()}
                      </Badge>
                      <span className="font-data text-lg text-white">
                        {hitRate.toFixed(1)}%
                      </span>
                    </div>
                    <div className="text-sm text-zinc-400">
                      {data.covered} / {data.total} covered
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Filters */}
      <div className="flex gap-4">
        <Select value={signalFilter} onValueChange={setSignalFilter}>
          <SelectTrigger className="w-40 bg-zinc-900 border-zinc-700" data-testid="signal-filter">
            <SelectValue placeholder="Signal" />
          </SelectTrigger>
          <SelectContent className="bg-zinc-900 border-zinc-700">
            <SelectItem value="all">Todos</SelectItem>
            <SelectItem value="green">Verde</SelectItem>
            <SelectItem value="yellow">Amarillo</SelectItem>
            <SelectItem value="red">Rojo</SelectItem>
          </SelectContent>
        </Select>

        <Select value={coveredFilter} onValueChange={setCoveredFilter}>
          <SelectTrigger className="w-40 bg-zinc-900 border-zinc-700" data-testid="covered-filter">
            <SelectValue placeholder="Covered" />
          </SelectTrigger>
          <SelectContent className="bg-zinc-900 border-zinc-700">
            <SelectItem value="all">Todos</SelectItem>
            <SelectItem value="true">Covered</SelectItem>
            <SelectItem value="false">Not Covered</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Predictions Table */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white">
            Historial de Predicciones ({predictions.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {predictions.length === 0 ? (
            <div className="text-center py-12">
              <HistoryIcon className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
              <p className="text-zinc-400">No hay predicciones con resultados</p>
              <p className="text-sm text-zinc-500 mt-1">
                Los resultados se actualizan automáticamente tras los partidos
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Fecha
                    </th>
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Partido
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Pred.
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Spread
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Edge
                    </th>
                    <th className="text-center text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Signal
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Actual
                    </th>
                    <th className="text-center text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Result
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {predictions.map((pred) => (
                    <tr key={pred.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                      <td className="p-3 text-sm text-zinc-400">
                        {formatDate(pred.created_at)}
                      </td>
                      <td className="p-3">
                        <span className="font-headings font-semibold text-white text-sm">
                          {pred.home_team}
                        </span>
                        <span className="text-zinc-500 mx-2">vs</span>
                        <span className="font-headings font-semibold text-zinc-300 text-sm">
                          {pred.away_team}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className="font-data text-white">
                          {formatSpread(pred.pred_margin)}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className="font-data text-blue-400">
                          {formatSpread(pred.market_spread_used)}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className={`font-data font-bold ${
                          pred.signal === 'green' ? 'text-green-500' :
                          pred.signal === 'yellow' ? 'text-yellow-500' : 'text-red-500'
                        }`}>
                          {formatSpread(pred.edge_points)}
                        </span>
                      </td>
                      <td className="p-3 text-center">
                        <Badge className={`${getSignalBadgeClass(pred.signal)} border`}>
                          {pred.signal?.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="p-3 text-right">
                        <span className="font-data text-white">
                          {pred.actual_margin != null ? formatSpread(pred.actual_margin) : '-'}
                        </span>
                      </td>
                      <td className="p-3 text-center">
                        {pred.covered != null ? (
                          pred.covered ? (
                            <CheckCircle className="w-5 h-5 text-green-500 mx-auto" />
                          ) : (
                            <XCircle className="w-5 h-5 text-red-500 mx-auto" />
                          )
                        ) : (
                          <span className="text-zinc-500">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
