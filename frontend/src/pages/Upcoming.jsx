import React, { useState, useEffect } from 'react';
import { adminApi, userApi } from '../lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { toast } from 'sonner';
import { 
  Calendar, 
  Loader2, 
  RefreshCw,
  Clock,
  MapPin
} from 'lucide-react';
import { formatDateTime, formatSpread, formatOdds } from '../lib/utils';

export default function Upcoming() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    loadEvents();
  }, []);

  const loadEvents = async () => {
    try {
      const response = await userApi.getUpcoming();
      setEvents(response.data.events || []);
    } catch (error) {
      console.error('Error loading events:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSyncOdds = async () => {
    setSyncing(true);
    try {
      await adminApi.syncUpcoming(2);
      const response = await adminApi.syncOdds(2);
      toast.success(response.data.message);
      loadEvents();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error al sincronizar odds');
    } finally {
      setSyncing(false);
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
    <div className="space-y-6" data-testid="upcoming-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
            Upcoming
          </h1>
          <p className="text-zinc-400 mt-1">Próximos partidos NBA con líneas de mercado</p>
        </div>
        <Button
          onClick={handleSyncOdds}
          disabled={syncing}
          className="bg-primary hover:bg-primary/90"
          data-testid="sync-odds-btn"
        >
          {syncing ? (
            <Loader2 className="w-4 h-4 animate-spin mr-2" />
          ) : (
            <RefreshCw className="w-4 h-4 mr-2" />
          )}
          Sincronizar Odds
        </Button>
      </div>

      {/* Events List */}
      {events.length === 0 ? (
        <Card className="bg-card border-border">
          <CardContent className="py-16 text-center">
            <Calendar className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
            <h3 className="font-headings text-xl text-zinc-400 mb-2">
              No hay partidos programados
            </h3>
            <p className="text-sm text-zinc-500 mb-4">
              Sincroniza las odds para ver los próximos partidos
            </p>
            <Button
              onClick={handleSyncOdds}
              disabled={syncing}
              className="bg-primary hover:bg-primary/90"
            >
              Sincronizar Ahora
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {events.map((event) => (
            <Card key={event.event_id} className="bg-card border-border">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="font-headings text-lg text-white">
                      <span className="text-primary">{event.home_team}</span>
                      <span className="text-zinc-500 mx-3">vs</span>
                      <span>{event.away_team}</span>
                    </CardTitle>
                    <CardDescription className="flex items-center gap-4 mt-1">
                      <span className="flex items-center gap-1 text-zinc-400">
                        <Clock className="w-4 h-4" />
                        {formatDateTime(event.commence_time)}
                      </span>
                      <span className="flex items-center gap-1 text-zinc-500">
                        <MapPin className="w-4 h-4" />
                        Europe/Madrid
                      </span>
                    </CardDescription>
                  </div>
                  {event.reference_line && (
                    <div className="text-right">
                      <p className="text-xs text-zinc-500 uppercase tracking-wider">Ref. Line</p>
                      <p className="font-data text-xl text-white">
                        {formatSpread(event.reference_line.spread_point_home)}
                      </p>
                      <p className="text-xs text-zinc-500">
                        {event.reference_line.bookmaker_title}
                      </p>
                    </div>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                {event.lines && event.lines.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-zinc-800">
                          <th className="text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 p-2">
                            Bookmaker
                          </th>
                          <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-2">
                            {event.home_team} Spread
                          </th>
                          <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-2">
                            Precio
                          </th>
                          <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-2">
                            {event.away_team} Spread
                          </th>
                          <th className="text-right text-xs font-semibold uppercase tracking-wider text-zinc-500 p-2">
                            Precio
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {event.lines.map((line, idx) => (
                          <tr 
                            key={idx} 
                            className={`border-b border-zinc-800/50 hover:bg-zinc-800/30 ${
                              event.reference_line?.bookmaker_key === line.bookmaker_key 
                                ? 'bg-primary/5' 
                                : ''
                            }`}
                          >
                            <td className="p-2">
                              <div className="flex items-center gap-2">
                                <span className="text-sm text-white">
                                  {line.bookmaker_title}
                                </span>
                                {event.reference_line?.bookmaker_key === line.bookmaker_key && (
                                  <Badge className="bg-primary/10 text-primary text-xs">
                                    REF
                                  </Badge>
                                )}
                              </div>
                            </td>
                            <td className="p-2 text-right">
                              <span className="font-data text-white">
                                {formatSpread(line.spread_point_home)}
                              </span>
                            </td>
                            <td className="p-2 text-right">
                              <span className="font-data text-blue-400">
                                {formatOdds(line.price_home_decimal)}
                              </span>
                            </td>
                            <td className="p-2 text-right">
                              <span className="font-data text-white">
                                {formatSpread(line.spread_point_away)}
                              </span>
                            </td>
                            <td className="p-2 text-right">
                              <span className="font-data text-blue-400">
                                {formatOdds(line.price_away_decimal)}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm text-zinc-500 text-center py-4">
                    No hay líneas disponibles para este partido
                  </p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
