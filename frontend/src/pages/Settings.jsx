import React from 'react';
import { useAuth } from '../context/AuthContext';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { 
  Settings as SettingsIcon, 
  User,
  Info,
  ExternalLink
} from 'lucide-react';

export default function Settings() {
  const { user } = useAuth();

  return (
    <div className="space-y-6" data-testid="settings-page">
      {/* Header */}
      <div>
        <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
          Settings
        </h1>
        <p className="text-zinc-400 mt-1">Configuración de la cuenta y del sistema</p>
      </div>

      {/* User Info */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
            <User className="w-5 h-5 text-primary" />
            Usuario
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Nombre</p>
              <p className="text-white">{user?.name}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Email</p>
              <p className="text-white">{user?.email}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* System Info */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
            <Info className="w-5 h-5 text-secondary" />
            Información del Sistema
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Detalles de configuración del modelo y datos
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="p-3 bg-zinc-900/50 rounded-md">
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Temporadas</p>
              <div className="flex flex-wrap gap-2">
                {['2021-22', '2022-23', '2023-24', '2024-25'].map((s) => (
                  <Badge key={s} variant="outline" className="text-xs">
                    {s}
                  </Badge>
                ))}
              </div>
            </div>
            <div className="p-3 bg-zinc-900/50 rounded-md">
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Feature Window</p>
              <p className="text-white font-data">15 partidos (rolling)</p>
            </div>
            <div className="p-3 bg-zinc-900/50 rounded-md">
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Modelo</p>
              <p className="text-white">Ridge Regression + StandardScaler</p>
            </div>
            <div className="p-3 bg-zinc-900/50 rounded-md">
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Timezone</p>
              <p className="text-white">Europe/Madrid</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Signal Configuration */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white flex items-center gap-2">
            <SettingsIcon className="w-5 h-5 text-yellow-500" />
            Configuración de Señales
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Umbrales para clasificación de edge
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 bg-green-500/5 border border-green-500/20 rounded-md">
              <div className="flex items-center gap-3">
                <div className="w-3 h-3 rounded-full bg-green-500"></div>
                <span className="text-white font-medium">Verde (Value)</span>
              </div>
              <span className="font-data text-green-500">|edge| ≥ 3.0 pts</span>
            </div>
            <div className="flex items-center justify-between p-3 bg-yellow-500/5 border border-yellow-500/20 rounded-md">
              <div className="flex items-center gap-3">
                <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                <span className="text-white font-medium">Amarillo (Watch)</span>
              </div>
              <span className="font-data text-yellow-500">2.0 ≤ |edge| &lt; 3.0 pts</span>
            </div>
            <div className="flex items-center justify-between p-3 bg-red-500/5 border border-red-500/20 rounded-md">
              <div className="flex items-center gap-3">
                <div className="w-3 h-3 rounded-full bg-red-500"></div>
                <span className="text-white font-medium">Rojo (No Value)</span>
              </div>
              <span className="font-data text-red-500">|edge| &lt; 2.0 pts</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Bookmakers */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white">
            Bookmakers
          </CardTitle>
          <CardDescription className="text-zinc-400">
            Casas de apuestas para líneas de mercado (EU/UK)
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {['Pinnacle', 'Betfair Exchange', 'William Hill', '888sport', 'Matchbook', 'Betway'].map((b) => (
              <Badge key={b} variant="outline" className="text-sm py-1 px-3">
                {b}
              </Badge>
            ))}
          </div>
          <p className="text-xs text-zinc-500 mt-3">
            Reference line: Pinnacle → Betfair → Mediana (precio cercano a 2.00)
          </p>
        </CardContent>
      </Card>

      {/* Data Sources */}
      <Card className="bg-card border-border">
        <CardHeader>
          <CardTitle className="font-headings text-xl text-white">
            Fuentes de Datos
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between p-3 bg-zinc-900/50 rounded-md">
            <div>
              <p className="text-white font-medium">nba_api</p>
              <p className="text-xs text-zinc-500">Datos históricos NBA (stats.nba.com)</p>
            </div>
            <a 
              href="https://github.com/swar/nba_api" 
              target="_blank" 
              rel="noopener noreferrer"
              className="text-zinc-400 hover:text-white"
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          </div>
          <div className="flex items-center justify-between p-3 bg-zinc-900/50 rounded-md">
            <div>
              <p className="text-white font-medium">The Odds API</p>
              <p className="text-xs text-zinc-500">Líneas de mercado en tiempo real</p>
            </div>
            <a 
              href="https://the-odds-api.com" 
              target="_blank" 
              rel="noopener noreferrer"
              className="text-zinc-400 hover:text-white"
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
