import React, { useState, useEffect } from 'react';
import { adminApi, statsApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Progress } from '../components/ui/progress';
import { toast } from 'sonner';
import { 
  Database, 
  Download, 
  Loader2, 
  CheckCircle,
  AlertCircle,
  RefreshCw
} from 'lucide-react';

const SEASONS = ['2021-22', '2022-23', '2023-24', '2024-25'];

export default function Dataset() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [buildingFeatures, setBuildingFeatures] = useState(false);

  useEffect(() => {
    loadStats();
  }, []);

  const loadStats = async () => {
    try {
      const response = await statsApi.getDataset();
      setStats(response.data);
    } catch (error) {
      console.error('Error loading stats:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const response = await adminApi.syncHistorical();
      toast.success(response.data.message);
      // Poll for updates
      setTimeout(loadStats, 5000);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error al sincronizar');
    } finally {
      setSyncing(false);
    }
  };

  const handleBuildFeatures = async () => {
    setBuildingFeatures(true);
    try {
      const response = await adminApi.buildFeatures();
      toast.success(response.data.message);
      loadStats();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error al construir features');
    } finally {
      setBuildingFeatures(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  const totalGames = stats?.total_games || 0;
  const totalFeatures = stats?.total_features || 0;
  const hasData = totalGames > 0;

  return (
    <div className="space-y-6" data-testid="dataset-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
            Dataset
          </h1>
          <p className="text-zinc-400 mt-1">Gestión de datos históricos NBA</p>
        </div>
        <Button
          onClick={loadStats}
          variant="ghost"
          className="text-zinc-400 hover:text-white"
          data-testid="refresh-stats-btn"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Actualizar
        </Button>
      </div>

      {/* Stats Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-card border-border">
          <CardContent className="p-6">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-md bg-primary/10">
                <Database className="w-6 h-6 text-primary" />
              </div>
              <div>
                <p className="font-data text-3xl font-bold text-white">
                  {totalGames.toLocaleString()}
                </p>
                <p className="text-sm text-zinc-500">Partidos Totales</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardContent className="p-6">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-md bg-secondary/10">
                <CheckCircle className="w-6 h-6 text-secondary" />
              </div>
              <div>
                <p className="font-data text-3xl font-bold text-white">
                  {totalFeatures.toLocaleString()}
                </p>
                <p className="text-sm text-zinc-500">Features Calculadas</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardContent className="p-6">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-md bg-yellow-500/10">
                <AlertCircle className="w-6 h-6 text-yellow-500" />
              </div>
              <div>
                <p className="font-data text-3xl font-bold text-white">
                  {SEASONS.length}
                </p>
                <p className="text-sm text-zinc-500">Temporadas</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Seasons Breakdown */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white">
            Temporadas Históricas
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Datos de partidos por temporada NBA
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {SEASONS.map((season) => {
              const count = stats?.by_season?.[season] || 0;
              const percentage = totalGames > 0 ? (count / totalGames) * 100 : 0;
              
              return (
                <div key={season} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-headings font-semibold text-white">{season}</span>
                    <span className="font-data text-sm text-zinc-400">
                      {count.toLocaleString()} partidos
                    </span>
                  </div>
                  <Progress value={percentage} className="h-2 bg-zinc-800" />
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Actions */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="font-headings text-lg text-white flex items-center gap-2">
              <Download className="w-5 h-5 text-primary" />
              Sincronizar Históricos
            </CardTitle>
            <CardDescription className="text-zinc-400">
              Descarga datos de partidos NBA desde nba_api
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-zinc-500 mb-4">
              Este proceso descargará resultados y estadísticas de las temporadas 
              2021-22 hasta 2024-25. Puede tardar varios minutos.
            </p>
            <Button
              onClick={handleSync}
              disabled={syncing}
              className="w-full bg-primary hover:bg-primary/90"
              data-testid="sync-historical-btn"
            >
              {syncing ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Sincronizando...
                </>
              ) : (
                <>
                  <Download className="w-4 h-4 mr-2" />
                  Sincronizar Datos
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="font-headings text-lg text-white flex items-center gap-2">
              <Database className="w-5 h-5 text-secondary" />
              Construir Features
            </CardTitle>
            <CardDescription className="text-zinc-400">
              Calcula features rolling para el modelo
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-zinc-500 mb-4">
              Genera features avanzadas (net rating, pace, eFG%, etc.) 
              usando una ventana rolling de 15 partidos.
            </p>
            <Button
              onClick={handleBuildFeatures}
              disabled={buildingFeatures || !hasData}
              className="w-full bg-secondary hover:bg-secondary/90"
              data-testid="build-features-btn"
            >
              {buildingFeatures ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Construyendo...
                </>
              ) : (
                <>
                  <Database className="w-4 h-4 mr-2" />
                  Construir Features
                </>
              )}
            </Button>
            {!hasData && (
              <p className="text-xs text-yellow-500 mt-2">
                Primero debes sincronizar los datos históricos
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Info */}
      <Card className="bg-zinc-900/50 border-border">
        <CardContent className="p-4">
          <div className="flex gap-3">
            <AlertCircle className="w-5 h-5 text-zinc-500 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-zinc-400">
              <p className="font-medium text-zinc-300 mb-1">Feature Window = 15</p>
              <p>
                Las features se calculan usando los últimos 15 partidos de cada equipo 
                (sin leakage). Incluyen: net_rating, pace, eFG%, TOV%, ORB%, FTr, 
                rest_days, is_b2b, y diferencias home-away.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
