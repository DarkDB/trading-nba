import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { statsApi, userApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { 
  Database, 
  Brain, 
  Target, 
  TrendingUp,
  Calendar,
  AlertCircle,
  Loader2,
  ArrowRight
} from 'lucide-react';
import { formatDateTime, getSignalBadgeClass } from '../lib/utils';

export default function Dashboard() {
  const [datasetStats, setDatasetStats] = useState(null);
  const [modelStats, setModelStats] = useState(null);
  const [picks, setPicks] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [datasetRes, modelRes, picksRes] = await Promise.all([
        statsApi.getDataset(),
        statsApi.getModel(),
        userApi.getPicks(),
      ]);
      setDatasetStats(datasetRes.data);
      setModelStats(modelRes.data);
      setPicks(picksRes.data.picks?.slice(0, 5) || []);
    } catch (error) {
      console.error('Error loading dashboard:', error);
    } finally {
      setLoading(false);
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
  const hasData = datasetStats?.total_games > 0;

  return (
    <div className="space-y-6" data-testid="dashboard">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
            Dashboard
          </h1>
          <p className="text-zinc-400 mt-1">Resumen de tu sistema de predicciones NBA</p>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="bg-card border-border">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-primary/10">
                <Database className="w-5 h-5 text-primary" />
              </div>
              <div>
                <p className="font-data text-2xl font-bold text-white">
                  {datasetStats?.total_games?.toLocaleString() || 0}
                </p>
                <p className="text-xs text-zinc-500 uppercase tracking-wider">Partidos</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-secondary/10">
                <Brain className="w-5 h-5 text-secondary" />
              </div>
              <div>
                <p className="font-data text-2xl font-bold text-white">
                  {hasModel ? modelStats.active_model.mae?.toFixed(1) : '-'}
                </p>
                <p className="text-xs text-zinc-500 uppercase tracking-wider">MAE Modelo</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-green-500/10">
                <Target className="w-5 h-5 text-green-500" />
              </div>
              <div>
                <p className="font-data text-2xl font-bold text-white">
                  {picks.filter(p => p.signal === 'green').length}
                </p>
                <p className="text-xs text-zinc-500 uppercase tracking-wider">Picks Verdes</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-yellow-500/10">
                <TrendingUp className="w-5 h-5 text-yellow-500" />
              </div>
              <div>
                <p className="font-data text-2xl font-bold text-white">
                  {picks.length}
                </p>
                <p className="text-xs text-zinc-500 uppercase tracking-wider">Total Picks</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Setup Status */}
      {(!hasData || !hasModel) && (
        <Card className="bg-card border-border border-yellow-500/30">
          <CardHeader className="pb-2">
            <CardTitle className="font-headings text-lg text-yellow-500 flex items-center gap-2">
              <AlertCircle className="w-5 h-5" />
              Configuración Pendiente
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {!hasData && (
                <div className="flex items-center justify-between p-3 bg-zinc-900/50 rounded-md">
                  <div className="flex items-center gap-3">
                    <Database className="w-5 h-5 text-zinc-500" />
                    <span className="text-sm text-zinc-300">Sincronizar datos históricos</span>
                  </div>
                  <Button 
                    size="sm" 
                    onClick={() => navigate('/dataset')}
                    className="bg-primary hover:bg-primary/90"
                    data-testid="setup-dataset-btn"
                  >
                    Ir a Dataset
                  </Button>
                </div>
              )}
              {hasData && !hasModel && (
                <div className="flex items-center justify-between p-3 bg-zinc-900/50 rounded-md">
                  <div className="flex items-center gap-3">
                    <Brain className="w-5 h-5 text-zinc-500" />
                    <span className="text-sm text-zinc-300">Entrenar modelo de predicción</span>
                  </div>
                  <Button 
                    size="sm" 
                    onClick={() => navigate('/train')}
                    className="bg-primary hover:bg-primary/90"
                    data-testid="setup-train-btn"
                  >
                    Ir a Train
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recent Picks */}
      <Card className="bg-card border-border">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="font-headings text-xl text-white">
            Picks Recientes
          </CardTitle>
          <Button 
            variant="ghost" 
            size="sm"
            onClick={() => navigate('/picks')}
            className="text-zinc-400 hover:text-white"
          >
            Ver todos <ArrowRight className="w-4 h-4 ml-1" />
          </Button>
        </CardHeader>
        <CardContent>
          {picks.length === 0 ? (
            <div className="text-center py-8">
              <Target className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
              <p className="text-zinc-400">No hay picks generados</p>
              <Button 
                className="mt-4 bg-primary hover:bg-primary/90"
                onClick={() => navigate('/picks')}
                data-testid="generate-picks-btn"
              >
                Generar Picks
              </Button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">Partido</th>
                    <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">Hora</th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">Pred.</th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">Spread</th>
                    <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">Edge</th>
                    <th className="text-center text-xs font-semibold uppercase tracking-wider text-zinc-500 p-3">Signal</th>
                  </tr>
                </thead>
                <tbody>
                  {picks.map((pick) => (
                    <tr key={pick.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
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
                        <span className="font-data text-white">
                          {pick.pred_margin > 0 ? '+' : ''}{pick.pred_margin?.toFixed(1)}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className="font-data text-blue-400">
                          {pick.market_spread_used > 0 ? '+' : ''}{pick.market_spread_used?.toFixed(1)}
                        </span>
                      </td>
                      <td className="p-3 text-right">
                        <span className={`font-data font-bold ${
                          pick.signal === 'green' ? 'text-green-500' :
                          pick.signal === 'yellow' ? 'text-yellow-500' : 'text-red-500'
                        }`}>
                          {pick.edge_points > 0 ? '+' : ''}{pick.edge_points?.toFixed(1)}
                        </span>
                      </td>
                      <td className="p-3 text-center">
                        <Badge className={`${getSignalBadgeClass(pick.signal)} border`}>
                          {pick.signal?.toUpperCase()}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Model Info */}
      {hasModel && (
        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
              <Brain className="w-5 h-5 text-secondary" />
              Modelo Activo
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Versión</p>
                <p className="font-data text-white">{modelStats.active_model.version}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">MAE</p>
                <p className="font-data text-white">{modelStats.active_model.mae?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">RMSE</p>
                <p className="font-data text-white">{modelStats.active_model.rmse?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Samples</p>
                <p className="font-data text-white">{modelStats.active_model.train_samples?.toLocaleString()}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
