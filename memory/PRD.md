# NBA Edge - PRD (Product Requirements Document)

## ⚠️ STATUS: PRODUCTION v4.0 – PAPER TRADING MODE ⚠️

**Version:** 4.0  
**Updated:** 04 Febrero 2026  
**Model Version:** v1.0  
**Selection Criterion:** Tiers by EV + Blowout Filter

---

## CAMBIO PRINCIPAL v4.0: Paper Trading con Tracking Completo

### Nuevas Funcionalidades
- **Configuración persistente**: Colección `trading_settings` en MongoDB
- **Filtro anti-blowout**: Excluye favoritos con pred_margin > threshold
- **Registro de resultados**: `POST /api/picks/:id/result` con WIN/LOSS/PUSH
- **Snapshot Close**: `POST /api/admin/snapshot-close` para closing lines y CLV
- **Simulación bankroll**: `GET /api/admin/report/bankroll-sim` con múltiples bankrolls
- **UI actualizada**: Panel Paper Trading v4.0 Stats con KPIs

---

## Endpoints Paper Trading v4.0

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/admin/trading/settings` | GET | Obtener configuración de trading |
| `/api/admin/trading/settings` | POST | Actualizar configuración |
| `/api/picks/:id/result` | POST | Registrar resultado final |
| `/api/admin/snapshot-close` | POST | Capturar closing lines |
| `/api/admin/report/bankroll-sim` | GET | Simulación de bankroll |

---

## Trading Settings Schema

```json
{
  "enabled_tiers": ["A", "B"],
  "blowout_filter_enabled": true,
  "blowout_pred_margin_threshold": 12.0,
  "stake_mode": "FLAT",
  "flat_stake_pct": 0.01,
  "kelly_fraction": 0.20,
  "kelly_cap_pct": 0.02
}
```

---

## Filtro Anti-Blowout

**Lógica:**
```python
is_favorite_pick = (
    (recommended_side == "HOME" and open_spread < 0) or
    (recommended_side == "AWAY" and open_spread > 0)
)

blowout_filter_hit = (
    blowout_filter_enabled and
    is_favorite_pick and
    abs(pred_margin) > blowout_threshold
)
```

**Campos añadidos por pick:**
- `is_favorite_pick`: boolean
- `spread_abs`: float
- `blowout_filter_hit`: boolean

---

## Registro de Resultados

**Input:**
```json
{
  "final_home_score": 110,
  "final_away_score": 105,
  "result_override": null
}
```

**Output:**
```json
{
  "result": "WIN",
  "margin_final": 5,
  "spread_adjusted_margin": 8.5,
  "covered": true,
  "profit_units": 0.96,
  "settled_at": "2026-02-04T09:46:06Z"
}
```

**Grading Logic:**
- HOME bet: covers if `margin_final + spread > 0`
- AWAY bet: covers if `margin_final + spread < 0`
- PUSH: `margin_final + spread == 0`

---

## Simulación Bankroll

**Request:**
```
GET /api/admin/report/bankroll-sim?bankrolls=1000,5000,10000&tiers=A,B&stake_mode=FLAT&blowout_filter=true
```

**Response:**
```json
{
  "bankroll_results": [
    {
      "initial_bankroll": 1000,
      "final_bankroll": 999.18,
      "profit": -0.82,
      "roi_pct": -0.08,
      "max_drawdown_pct": 1.5,
      "winrate_pct": 50.0
    }
  ],
  "tier_summary": {
    "A": {"wins": 0, "losses": 1, "winrate_pct": 0.0},
    "B": {"wins": 1, "losses": 0, "winrate_pct": 100.0}
  }
}
```

---

## ✅ P0 v4.0 COMPLETADO (04 Febrero 2026)

1. ✅ Trading settings persisten en MongoDB
2. ✅ Filtro anti-blowout implementado (excluye 1 pick en test)
3. ✅ Endpoint `/api/picks/:id/result` funcional (WIN/LOSS/PUSH)
4. ✅ Endpoint `/api/admin/snapshot-close` funcional
5. ✅ Endpoint `/api/admin/report/bankroll-sim` funcional
6. ✅ UI Paper Trading v4.0 Stats con KPIs
7. ✅ Settings persisten tras reinicio del servidor

---

## Fórmulas Implementadas

### Expected Value
```
EV = p_cover × open_price - 1
```

### Kelly Criterion (stake sizing)
```
kelly_optimal = (p_cover × (price-1) - (1-p_cover)) / (price-1)
stake = bankroll × min(kelly_fraction × kelly_optimal, kelly_cap_pct)
```

### CLV (Closing Line Value)
```
HOME bet: CLV = open_spread - close_spread
AWAY bet: CLV = close_spread - open_spread
```

---

## Code Architecture

```
/app/
├── backend/
│   ├── server.py       # ~3100 líneas con todos los endpoints
│   ├── .env
│   └── requirements.txt
├── frontend/
│   ├── src/pages/LiveOps.jsx  # UI principal con Paper Trading Stats
│   └── ...
└── memory/
    └── PRD.md
```

---

## BACKLOG

### P1 - Próximas Tareas
- Automatizar captura de closing lines (cron job)
- Añadir filtros de fecha en UI
- Export CSV de resultados

### P2 - Funcionalidades Pendientes
- Dashboard de performance histórico
- Gráfico de bankroll evolution
- Alertas de draws (drawdown threshold)

### P3 - Backlog
- Implementar `defensive_rating` real
- Multi-book arbitrage detection
- API para telegram/discord alerts
