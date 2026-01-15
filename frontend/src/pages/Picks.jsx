import React, { useState, useEffect } from 'react';
import { userApi, statsApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { toast } from 'sonner';
import { 
  Target, 
  Loader2, 
  RefreshCw,
  AlertCircle,
  Zap
} from 'lucide-react';
import { formatDateTime, formatSpread, getSignalBadgeClass } from '../lib/utils';

export default function Picks() {
  const [picks, setPicks] = useState([]);
  const [modelStats, setModelStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [picksRes, modelRes] = await Promise.all([
        userApi.getPicks(),
        statsApi.getModel(),
      ]);
      setPicks(picksRes.data.picks || []);
      setModelStats(modelRes.data);
    } catch (error) {
      console.error('Error loading picks:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const response = await userApi.generatePicks();
      toast.success(`Generados ${response.data.count} picks`);
      setPicks(response.data.picks || []);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error al generar picks');
    } finally {
      setGenerating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  const hasModel = modelStats?.active_model;

  // Group picks by signal
  const greenPicks = picks.filter(p => p.signal === 'green');
  const yellowPicks = picks.filter(p => p.signal === 'yellow');
  const redPicks = picks.filter(p => p.signal === 'red');

  return (
    <div className="space-y-6" data-testid="picks-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
            Picks
          </h1>
          <p className="text-zinc-400 mt-1">Predicciones generadas por el modelo</p>
        </div>
        <Button
          onClick={handleGenerate}
          disabled={generating || !hasModel}
          className="bg-primary hover:bg-primary/90"
          data-testid="generate-picks-btn"
        >
          {generating ? (
            <Loader2 className="w-4 h-4 animate-spin mr-2" />
          ) : (
            <Zap className="w-4 h-4 mr-2" />
          )}
          Generar Picks
        </Button>
      </div>

      {/* No Model Warning */}
      {!hasModel && (
        <Card className="bg-card border-border border-yellow-500/30">
          <CardContent className="py-4">
            <div className="flex items-center gap-3">
              <AlertCircle className="w-5 h-5 text-yellow-500" />
              <p className="text-sm text-yellow-500">
                Necesitas entrenar un modelo antes de generar picks. 
                Ve a la sección Train.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Signal Legend */}
      <Card className="bg-card border-border">
        <CardContent className="py-3">
          <div className="flex items-center justify-center gap-8">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-green-500 shadow-lg shadow-green-500/30"></div>
              <span className="text-sm text-zinc-400">Verde: |edge| ≥ 3.0</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-yellow-500 shadow-lg shadow-yellow-500/30"></div>
              <span className="text-sm text-zinc-400">Amarillo: 2.0 ≤ |edge| &lt; 3.0</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500 shadow-lg shadow-red-500/30"></div>
              <span className="text-sm text-zinc-400">Rojo: |edge| &lt; 2.0</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Stats Summary */}
      <div className="grid grid-cols-3 gap-4">
        <Card className="bg-card border-border">
          <CardContent className="py-4">
            <div className="text-center">
              <p className="font-data text-3xl font-bold text-green-500">{greenPicks.length}</p>
              <p className="text-xs text-zinc-500 uppercase tracking-wider mt-1">Value Picks</p>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-card border-border">
          <CardContent className="py-4">
            <div className="text-center">
              <p className="font-data text-3xl font-bold text-yellow-500">{yellowPicks.length}</p>
              <p className="text-xs text-zinc-500 uppercase tracking-wider mt-1">Watch List</p>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-card border-border">
          <CardContent className="py-4">
            <div className="text-center">
              <p className="font-data text-3xl font-bold text-red-500">{redPicks.length}</p>
              <p className="text-xs text-zinc-500 uppercase tracking-wider mt-1">No Value</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Picks Table */}
      {picks.length === 0 ? (
        <Card className="bg-card border-border">
          <CardContent className="py-16 text-center">
            <Target className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
            <h3 className="font-headings text-xl text-zinc-400 mb-2">
              No hay picks generados
            </h3>
            <p className="text-sm text-zinc-500 mb-4">
              Genera picks basados en las líneas actuales del mercado
            </p>
            {hasModel && (
              <Button
                onClick={handleGenerate}
                disabled={generating}
                className="bg-primary hover:bg-primary/90"
              >
                Generar Picks
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="font-headings text-xl text-white">
              Todos los Picks ({picks.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Partido
                    </th>
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Hora
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Pred. Margin
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Market Spread
                    </th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Edge
                    </th>
                    <th className="text-center text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Signal
                    </th>
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Book
                    </th>
                    <th className="text-center text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">
                      Conf.
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {picks.map((pick) => (
                    <tr 
                      key={pick.id} 
                      className={`border-b border-zinc-800/50 hover:bg-zinc-800/30 ${
                        pick.signal === 'green' ? 'bg-green-500/5' : ''
                      }`}
                    >
                      <td className="p-3">
                        <span className="font-headings font-semibold text-white">
                          {pick.home_team}
                        </span>
                        <span className="text-zinc-500 mx-2">vs</span>
                        <span className="font-headings font-semibold text-zinc-300">
                          {pick.away_team}
                        </span>
                      </td>
                      <td className="p-3 text-sm text-zinc-400">
                        {formatDateTime(pick.commence_time)}
                      </td>
                      <td className="p-3 text-right">
                        <span className="font-data text-white" data-testid="pred-margin">
                          {formatSpread(pick.pred_margin)}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className="font-data text-blue-400" data-testid="market-spread">
                          {formatSpread(pick.market_spread_used)}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className={`font-data font-bold ${
                          pick.signal === 'green' ? 'text-green-500' :
                          pick.signal === 'yellow' ? 'text-yellow-500' : 'text-red-500'
                        }`} data-testid="edge-value">
                          {formatSpread(pick.edge_points)}
                        </span>
                      </td>
                      <td className="p-3 text-center">
                        <Badge 
                          className={`${getSignalBadgeClass(pick.signal)} border`}
                          data-testid="signal-badge"
                        >
                          {pick.signal?.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="p-3 text-sm text-zinc-400">
                        {pick.reference_bookmaker_used}
                      </td>
                      <td className="p-3 text-center">
                        <Badge 
                          className={`${
                            pick.confidence === 'high' ? 'bg-green-500/10 text-green-400' :
                            pick.confidence === 'medium' ? 'bg-yellow-500/10 text-yellow-400' :
                            'bg-red-500/10 text-red-400'
                          } border-0 text-xs`}
                        >
                          {pick.confidence?.toUpperCase() || 'N/A'}
                        </Badge>
                      </td>
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
