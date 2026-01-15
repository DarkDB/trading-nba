# NBA Edge - Runbook Operativo v1.0

## Operativa Diaria

### Checklist Pre-Mercado (antes de las 00:00 CET)

1. **Sincronizar eventos próximos**
   ```bash
   # En Live Ops: Click "Sync Upcoming"
   # O via API:
   curl -X POST "$API_URL/api/admin/sync-upcoming?days=2" -H "Authorization: Bearer $TOKEN"
   ```

2. **Sincronizar odds (Pinnacle)**
   ```bash
   # En Live Ops: Click "Sync Odds"
   curl -X POST "$API_URL/api/admin/sync-odds?days=2" -H "Authorization: Bearer $TOKEN"
   ```

3. **Generar picks operativos**
   ```bash
   # En Live Ops: Click "Generate Picks" (con Modo Operativo ON)
   curl -X POST "$API_URL/api/picks/generate?operative_mode=true" -H "Authorization: Bearer $TOKEN"
   ```

4. **Revisar picks en pantalla Live Ops**
   - Ver "Today's Picks" y "Tomorrow's Picks"
   - Cada pick muestra: `recommended_bet_string` (ej: "LAL -4.5")
   - Verificar que `do_not_bet = false` y `signal = GREEN`

---

## Cómo Apostar

### Leer el Pick

Cada pick operativo muestra:

```
Apuesta Recomendada: LAL -4.5

pred_margin = +2.1    (modelo predice HOME gana por 2.1)
market_spread = -4.5  (mercado tiene HOME -4.5)
edge = +6.6           (pred_margin - market_spread)
```

### Convención de Signos

| Campo | Significado |
|-------|-------------|
| `market_spread = -4.5` | HOME es favorito por 4.5 puntos |
| `market_spread = +3.0` | HOME es underdog por 3 puntos |
| `pred_margin > 0` | Modelo predice victoria HOME |
| `pred_margin < 0` | Modelo predice victoria AWAY |
| `edge > 0` | Apostar HOME |
| `edge < 0` | Apostar AWAY |

### Regla de Decisión

```
SI edge > 0 → APOSTAR HOME (usar spread del HOME)
SI edge < 0 → APOSTAR AWAY (usar spread opuesto)
```

---

## Snapshot de Líneas de Cierre (T-60)

**60 minutos antes del partido:**

```bash
# En Live Ops: Click "Snapshot Close (T-60)"
curl -X POST "$API_URL/api/admin/snapshot-close-lines?minutes_before=60" -H "Authorization: Bearer $TOKEN"
```

Esto guarda:
- `close_spread`: spread de Pinnacle al cierre
- `close_price`: precio al cierre
- `clv_spread`: Closing Line Value (positivo = bueno)

---

## Filtros Operativos (v1.0)

| Filtro | Valor | Razón si falla |
|--------|-------|----------------|
| Signal | GREEN only | `NOT_GREEN_SIGNAL` |
| Edge mínimo | \|edge\| ≥ 3.5 | `EDGE_TOO_SMALL` |
| Confianza | HIGH only | `LOW_CONFIDENCE` |
| Pinnacle | Requerido | `NO_PINNACLE_LINE` |
| Max picks/día | 2 | (se ordenan por edge) |

---

## Registrar Resultado

Después del partido:

1. Ver resultado final (score)
2. Calcular `actual_margin = home_pts - away_pts`
3. Determinar `covered`:
   - Si apostaste HOME -4.5 y actual_margin >= -4.5 → COVERED
   - Si apostaste AWAY +4.5 y actual_margin <= 4.5 → COVERED

*Nota: La actualización automática de resultados está pendiente de implementar.*

---

## Métricas del Modelo

Ver en "Model Health" card:

| Métrica | Descripción | Target |
|---------|-------------|--------|
| MAE | Error absoluto medio | < 10 |
| RMSE | Root mean squared error | < 13 |
| pred_std_test | Std de predicciones en test | > 2.0 |
| model_version | Timestamp del modelo | - |
| data_cutoff_date | Última fecha de datos | Reciente |

---

## Troubleshooting

### "No hay picks operativos"
- Verificar que hay eventos sincronizados
- Verificar que hay odds de Pinnacle
- El modelo puede estar descartando todos por filtros

### "NO_PINNACLE_LINE"
- Pinnacle no tiene línea para ese partido
- No apostar (do_not_bet = true)

### "EDGE_TOO_SMALL"
- El edge es < 3.5 puntos
- No hay suficiente valor

### "LOW_CONFIDENCE"
- Un equipo tiene < 15 partidos de historial
- Predicción menos fiable

---

## Config Snapshot (v1.0)

```json
{
  "rolling_window_n": 15,
  "algorithm": "Ridge",
  "alpha": 1.0,
  "signal_thresholds": {"green": 3.0, "yellow": 2.0},
  "operative_thresholds": {
    "min_edge": 3.5,
    "max_picks_per_day": 2,
    "require_high_confidence": true,
    "require_green_signal": true,
    "require_pinnacle": true
  },
  "spread_convention": "HOME_PERSPECTIVE_SIGNED"
}
```

---

## Versionado

- Cada entrenamiento crea una NUEVA versión de modelo
- Las predicciones guardan `model_id` y `model_version`
- Los config_snapshot son inmutables por versión
- Los modelos antiguos se desactivan pero NO se borran
