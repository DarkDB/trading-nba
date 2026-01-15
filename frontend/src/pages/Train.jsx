import React, { useState, useEffect } from 'react';
import { adminApi, statsApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { toast } from 'sonner';
import { 
  Brain, 
  Loader2, 
  CheckCircle,
  AlertCircle,
  Activity,
  Zap
} from 'lucide-react';

export default function Train() {
  const [modelStats, setModelStats] = useState(null);
  const [datasetStats, setDatasetStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [training, setTraining] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [modelRes, datasetRes] = await Promise.all([
        statsApi.getModel(),
        statsApi.getDataset(),
      ]);
      setModelStats(modelRes.data);
      setDatasetStats(datasetRes.data);
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleTrain = async () => {
    setTraining(true);
    try {
      const response = await adminApi.train();
      if (response.data.status === 'error') {
        toast.error(response.data.message);
      } else {
        toast.success(response.data.message);
        loadData();
      }
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error al entrenar el modelo');
    } finally {
      setTraining(false);
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
  const hasFeatures = datasetStats?.total_features > 0;
  const model = modelStats?.active_model;

  return (
    <div className="space-y-6" data-testid="train-page">
      {/* Header */}
      <div>
        <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
          Train
        </h1>
        <p className="text-zinc-400 mt-1">Entrenamiento del modelo de predicción</p>
      </div>

      {/* Model Status */}
      <Card className="bg-card border-border">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
              <Brain className="w-5 h-5 text-secondary" />
              Estado del Modelo
            </CardTitle>
            {hasModel && (
              <Badge className="bg-green-500/10 text-green-500 border-green-500/20">
                ACTIVO
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {hasModel ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Versión</p>
                <p className="font-data text-lg text-white">{model.version}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">MAE (Test)</p>
                <p className="font-data text-lg text-white">{model.mae?.toFixed(3)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">RMSE (Test)</p>
                <p className="font-data text-lg text-white">{model.rmse?.toFixed(3)}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Feature Window</p>
                <p className="font-data text-lg text-white">{model.feature_window}</p>
              </div>
            </div>
          ) : (
            <div className="text-center py-8">
              <AlertCircle className="w-12 h-12 text-yellow-500 mx-auto mb-3" />
              <p className="text-zinc-400">No hay modelo entrenado</p>
              <p className="text-sm text-zinc-500 mt-1">
                Entrena un modelo para generar predicciones
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Model Details */}
      {hasModel && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card className="bg-card border-border">
            <CardHeader>
              <CardTitle className="font-headings text-lg text-white">
                Datos de Entrenamiento
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Train Seasons</span>
                <span className="font-data text-white">
                  {model.train_seasons?.join(', ')}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Test Season</span>
                <span className="font-data text-white">{model.test_season}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Train Samples</span>
                <span className="font-data text-white">
                  {model.train_samples?.toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Test Samples</span>
                <span className="font-data text-white">
                  {model.test_samples?.toLocaleString()}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardHeader>
              <CardTitle className="font-headings text-lg text-white">
                Métricas de Error
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Train MAE</span>
                <span className="font-data text-white">{model.train_mae?.toFixed(3)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Train RMSE</span>
                <span className="font-data text-white">{model.train_rmse?.toFixed(3)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Test MAE</span>
                <span className="font-data text-green-500 font-bold">{model.mae?.toFixed(3)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-zinc-400">Test RMSE</span>
                <span className="font-data text-green-500 font-bold">{model.rmse?.toFixed(3)}</span>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Train Action */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-lg text-white flex items-center gap-2">
            <Zap className="w-5 h-5 text-primary" />
            Entrenar Modelo
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Ridge Regression con StandardScaler
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div className="p-3 bg-zinc-900/50 rounded-md">
                <p className="text-zinc-500 mb-1">Algoritmo</p>
                <p className="text-white font-medium">Ridge Regression (α=1.0)</p>
              </div>
              <div className="p-3 bg-zinc-900/50 rounded-md">
                <p className="text-zinc-500 mb-1">Split Temporal</p>
                <p className="text-white font-medium">Train: 21-24 / Test: 24-25</p>
              </div>
            </div>

            <div className="p-3 bg-zinc-900/50 rounded-md">
              <p className="text-zinc-500 mb-2">Features</p>
              <div className="flex flex-wrap gap-2">
                {['diff_net_rating', 'diff_pace', 'diff_efg', 'diff_tov_pct', 
                  'diff_orb_pct', 'diff_ftr', 'diff_rest', 'home_advantage'].map((f) => (
                  <Badge key={f} variant="outline" className="text-xs">
                    {f}
                  </Badge>
                ))}
              </div>
            </div>

            <Button
              onClick={handleTrain}
              disabled={training || !hasFeatures}
              className="w-full bg-primary hover:bg-primary/90 h-12"
              data-testid="train-model-btn"
            >
              {training ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Entrenando Modelo...
                </>
              ) : (
                <>
                  <Brain className="w-4 h-4 mr-2" />
                  {hasModel ? 'Re-entrenar Modelo' : 'Entrenar Modelo'}
                </>
              )}
            </Button>

            {!hasFeatures && (
              <p className="text-xs text-yellow-500 text-center">
                Primero debes construir las features en la sección Dataset
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Info */}
      <Card className="bg-zinc-900/50 border-border">
        <CardContent className="p-4">
          <div className="flex gap-3">
            <Activity className="w-5 h-5 text-zinc-500 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-zinc-400">
              <p className="font-medium text-zinc-300 mb-1">Sobre el Modelo</p>
              <p>
                El modelo predice el margen esperado (home_pts - away_pts) basándose 
                en features rolling de los últimos 15 partidos. El MAE indica el error 
                absoluto promedio en puntos.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
