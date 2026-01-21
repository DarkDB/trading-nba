# NBA Edge - PRD (Product Requirements Document)

## ⚠️ STATUS: PRODUCTION v2.0 – PROBABILITY + EV MODE ⚠️

**Version:** 2.0  
**Updated:** 18 Enero 2025  
**Model Version:** v1.0  
**Selection Criterion:** EV ≥ 2% (probabilistic)

---

## CAMBIO PRINCIPAL v2.0: Edge → Probabilidad + EV

### Antes (v1.x)
- Selección por `betting_edge` en puntos (≥3.5)
- Signal: GREEN/YELLOW/RED por edge

### Ahora (v2.0)
- Selección por **EV (Expected Value)** ≥ 2%
- `p_cover` = probabilidad de cubrir spread (usando distribución normal)
- `EV = p_cover × price - 1`
- Sigma calibrado desde residuos históricos

---

## Fórmulas Implementadas

### Probabilidad de Cover (ATS)
```
cover_threshold = -market_spread
z = (pred_margin - cover_threshold) / sigma

Si pick=HOME: p_cover = NormalCDF(z)
Si pick=AWAY: p_cover = NormalCDF(-z)
```

### Expected Value
```
EV = p_cover × open_price - 1
```

### Criterios de Selección (v2.0)
- EV ≥ 0.02 (2%)
- confidence = HIGH
- Pinnacle required
- Sin límite de picks/día

---

## Nuevas Columnas por Pick

| Columna | Descripción |
|---------|-------------|
| `sigma` | Desviación estándar del modelo (12.0 por defecto) |
| `p_cover` | Probabilidad de cubrir el spread (0-1) |
| `implied_prob` | 1/price - probabilidad implícita del mercado |
| `ev` | Expected Value = p_cover × price - 1 |
| `signal_ev` | green (EV≥5%), yellow (EV≥2%), red (EV<2%) |

---

## Endpoints Nuevos

| Endpoint | Descripción |
|----------|-------------|
| `POST /api/admin/model/sigma/recompute` | Recalcular sigma desde históricos |
| `GET /api/admin/model/sigma` | Obtener calibración actual |

---

## Tests Añadidos (19 total)

- `test_normal_cdf` - Implementación CDF normal
- `test_probability_monotonicity` - Mayor edge → mayor p_cover
- `test_ev_sign` - EV positivo cuando p_cover > implied_prob
- `test_sigma_reasonable_range` - 8 ≤ sigma ≤ 20
- `test_p_cover_boundary_cases` - p_cover ~50% en threshold

---

## UI Actualizada (Live Ops)

Nueva tabla "All Valid Picks" con columnas:
- Partido, Apuesta, Price, Impl%, p_cover, **EV%**, Pred, Thresh, σ

Ordenado por EV descendente.

---

## Configuración Operativa

```python
OPERATIONAL_CONFIG = {
    "version": "2.0",
    "operative_thresholds": {
        "min_ev": 0.02,  # 2% mínimo
        "require_positive_ev": True,
        "require_high_confidence": True,
        "require_pinnacle": True,
        "max_picks_per_day": None  # Sin límite
    },
    "calibration": {
        "sigma_global": 12.0,  # Default, recalcular con /sigma/recompute
        "sigma_source": "default"
    }
}
```

---

## Notas de Calibración

- **Sigma por defecto:** 12.0 (valor típico NBA)
- **Para recalibrar:** Ejecutar `POST /api/admin/model/sigma/recompute` después de build-features
- **Rango esperado:** 8 ≤ sigma ≤ 20
- **Si sigma fuera de rango:** Revisar calidad de predicciones

---

## Ejemplo de Pick v2.0

```
Apuesta: MIN +4.0
Price: 1.90
Implied Prob: 52.6%
p_cover: 60.0%
EV: +14.0%
Pred: +7.8
Threshold: -4.0
Sigma: 12.0
```

---

## FROZEN BACKLOG

- Automatización de resultados
- defensive_rating real
- Devigging de odds
- Dashboard de performance histórico
