# NBA Edge - PRD (Product Requirements Document)

## ⚠️ STATUS: PRODUCTION v3.0 – PAPER TRADING MODE ⚠️

**Version:** 3.0  
**Updated:** 23 Enero 2026  
**Model Version:** v1.0  
**Selection Criterion:** Tiers by EV (A≥5%, B:2-5%, C:-1% to +1%)

---

## CAMBIO PRINCIPAL v3.0: Paper Trading Mode

### Antes (v2.0)
- Selección por `EV ≥ 2%`
- Sigma calibrado en memoria
- Sin trazabilidad completa

### Ahora (v3.0)
- **Persistencia total**: Calibración en MongoDB (`calibrations` collection)
- **Trazabilidad completa**: Cada pick guarda `calibration_id` y todos los parámetros
- **Shrinkage Bayesiano**: `beta_effective` y `alpha_effective` ponderados con priors conservadores
- **Picks por Tiers**: A (EV≥5%), B (2%≤EV<5%), C (-1%≤EV≤+1%)
- **Sin límite diario**: Máximo volumen para paper trading

---

## Fórmulas Implementadas (v3.0)

### VS_MARKET Calibration
```
model_edge = pred_margin - cover_threshold
adjusted_edge = beta_effective × model_edge + alpha_effective
z = adjusted_edge / sigma_residual

Si pick=HOME: p_cover = NormalCDF(z)
Si pick=AWAY: p_cover = NormalCDF(-z)
```

### Shrinkage Bayesiano
```python
# Weight based on sample size
w = n_spread_samples / (n_spread_samples + k)  # k=200

# Effective values (weighted average with priors)
beta_effective = w × beta_reg + (1-w) × beta_prior  # beta_prior=0.25
alpha_effective = w × alpha_reg + (1-w) × alpha_prior  # alpha_prior=0.0

# Safety clamps
beta_effective = clamp(beta_effective, 0.15, 0.65)
alpha_effective = clamp(alpha_effective, -3.0, 3.0)
```

### Expected Value
```
EV = p_cover × open_price - 1
```

### Tier Classification
| Tier | EV Range | Purpose |
|------|----------|---------|
| A | EV ≥ 5% | Core picks - Highest expected value |
| B | 2% ≤ EV < 5% | Exploration picks - Moderate edge |
| C | -1% ≤ EV ≤ +1% | Control picks - Baseline/validation |

---

## Campos por Pick (v3.0)

| Columna | Descripción |
|---------|-------------|
| `calibration_id` | ID de la calibración usada |
| `probability_mode` | "VS_MARKET" |
| `beta_used` | beta_effective usado |
| `alpha_used` | alpha_effective usado |
| `sigma_used` | sigma_residual usado |
| `w_used` | Peso del shrinkage |
| `k_used` | Constante de shrinkage |
| `beta_reg` | beta de regresión (raw) |
| `beta_prior` | prior de beta (0.25) |
| `alpha_reg` | alpha de regresión (raw) |
| `alpha_prior` | prior de alpha (0.0) |
| `beta_effective` | beta efectivo final |
| `alpha_effective` | alpha efectivo final |
| `open_spread` | Spread de apertura |
| `open_price` | Precio de apertura |
| `implied_prob` | Probabilidad implícita |
| `p_cover` | Probabilidad de cubrir |
| `ev` | Expected Value |
| `tier` | A, B o C |
| `book` | Casa de apuestas (Pinnacle) |

---

## Endpoints (v3.0)

| Endpoint | Descripción |
|----------|-------------|
| `POST /api/admin/model/calibrate-vs-market` | Crear nueva calibración VS_MARKET |
| `GET /api/admin/calibration/current` | Obtener calibración activa con todos los campos |
| `POST /api/picks/generate` | Generar picks por tiers (Paper Trading v3.0) |
| `GET /api/audit/model-sanity` | Auditoría de sanidad del modelo |
| `POST /api/admin/calibration/lock` | Bloquear calibración |

---

## Colección `calibrations` Schema

```json
{
  "calibration_id": "calib_20260123_090928",
  "probability_mode": "VS_MARKET",
  "beta_effective": 0.25,
  "alpha_effective": 0.2982,
  "sigma_residual": 14.55,
  "beta_reg": 1.0019,
  "beta_prior": 0.25,
  "alpha_reg": 2.1057,
  "alpha_prior": 0.0,
  "k_used": 200,
  "w_used": 0.1416,
  "beta_clamped": true,
  "alpha_clamped": false,
  "n_spread_samples": 33,
  "n_residual_samples": 729,
  "beta_source": "regression",
  "sigma_source": "historical_residuals",
  "computed_at": "2026-01-23T09:09:28.064926+00:00",
  "data_cutoff": "2026-01-23",
  "model_version": "20260123_084651",
  "is_active": true,
  "is_locked": false,
  "is_auditable": true
}
```

---

## UI Live Ops (v3.0)

- **Header**: Botones de Tier selector (A, B, C) con conteos
- **Calibration Panel**: Muestra todos los parámetros de shrinkage
- **Tier Thresholds**: Badges explicando cada tier
- **Picks Table**: Columnas Partido, Tier, Apuesta, Price, Impl%, p_cover, EV%, Pred, σ, Calib ID
- **PickCard**: Muestra calibration_id, β, σ, w en cada pick

---

## ✅ P0 COMPLETADO (23 Enero 2026)

1. ✅ Calibración persiste en MongoDB
2. ✅ No-regresión: 3 reinicios sin cambio de valores
3. ✅ Generate picks con tiers (A:26, B:9, C:4)
4. ✅ Trazabilidad completa por pick
5. ✅ UI actualizada con tier selector
6. ✅ Audit y Generate usan mismo calibration_id

---

## BACKLOG (P1-P3)

### P1 - Próximas Tareas
- `POST /api/picks/:id/result` - Registrar resultados manuales
- Snapshot Close (T-60) - Guardar closing lines

### P2 - Funcionalidades Pendientes
- CLV automático (Closing Line Value)
- Reportes de rendimiento por Tier
- Export CSV mejorado

### P3 - Backlog
- Implementar `defensive_rating` real
- Dashboard de performance histórico
- Automatización de resultados
