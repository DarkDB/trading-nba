# NBA Edge - PRD (Product Requirements Document)

## ⚠️ STATUS: PRODUCTION LOCKED – READY FOR LIVE PICKS ⚠️

**Locked Date:** 15 Enero 2025  
**Model Version:** v1.0  
**Cover Logic Version:** v1.1 (BUG FIX VERIFIED)
**Operative Config:** LOCKED  

> ⛔ NO realizar cambios de código ni modelo sin instrucción explícita del usuario.

---

## CRITICAL BUG FIX (15 Jan 2025)

### ❌ Bug Reportado
El sistema recomendaba apuestas que el modelo NO cubría:
- ORL vs MEM: pred=+0.75, spread=-5.0 → Recomendaba ORL -5.0 (INCORRECTO)
- SAS vs MIL: pred=-0.97, spread=-7.5 → Recomendaba SAS -7.5 (INCORRECTO)
- GSW vs NYK: pred=+2.56, spread=-7.5 → Recomendaba GSW -7.5 (INCORRECTO)

### ✅ Lógica Corregida (v1.1)
```
cover_threshold = -market_spread
HOME cubre si: pred_margin > cover_threshold
AWAY cubre si: pred_margin < cover_threshold
edge = |pred_margin - cover_threshold| (siempre positivo)
```

### Verificación Completa
| Caso | pred | spread | threshold | Resultado | Status |
|------|------|--------|-----------|-----------|--------|
| ORL vs MEM | +0.75 | -5.0 | +5.0 | AWAY (MEM +5.0), edge=4.25 | ✅ |
| SAS vs MIL | -0.97 | -7.5 | +7.5 | AWAY (MIL +7.5), edge=8.47 | ✅ |
| **GSW vs NYK** | +2.56 | -7.5 | +7.5 | AWAY (NYK +7.5), edge=4.94 | ✅ |
| LAL vs CHA | +8.27 | -4.5 | +4.5 | HOME (LAL -4.5), edge=3.77 | ✅ |

### Tests Añadidos
- `test_no_home_pick_if_pred_margin_does_not_cover_spread` - Test específico para GSW vs NYK
- `test_no_bet_when_model_does_not_cover` - Tests para ORL y SAS
- `test_edge_positive_and_consistent_with_side` - Validación de edge siempre positivo

### Resultado Final Tests
- Unit Tests Production: **14/14 passed (100%)**

---

## Original Problem Statement
Construir una web app full-stack llamada "NBA Edge" orientada a operar desde España (odds en formato decimal). La app aprende con históricos NBA para predecir el margen esperado (home_pts - away_pts) y detecta value comparando con el mercado de spreads.

## Technology Stack
- **Backend**: FastAPI (Python) 
- **Frontend**: React + Tailwind + shadcn/ui
- **Database**: MongoDB
- **Auth**: JWT simple
- **External APIs**: nba_api, The Odds API

---

## OPERATIVE PICKS VERIFICADOS (Live Ops)

Los picks operativos actuales (con filtros: GREEN, edge>=3.5, HIGH, Pinnacle, max 2):

1. **Miami Heat vs Boston Celtics**
   - Apuesta: MIA +2.0 (HOME)
   - pred=+7.56, edge=9.56

2. **San Antonio Spurs vs Milwaukee Bucks**
   - Apuesta: MIL +7.5 (AWAY)
   - pred=-0.97, edge=8.47

**GSW vs NYK NO aparece** en picks operativos (edge=4.94 < edge de los top 2).

---

## FROZEN BACKLOG (NO IMPLEMENTAR SIN AUTORIZACIÓN)

- Automatización de resultados
- defensive_rating real
- Más temporadas históricas
- Dashboard de monitorización

---

## Test Reports
- `/app/test_reports/iteration_3.json` - Verificación bug fix inicial
- `/app/backend/tests/test_production.py` - 14 tests incluyendo los nuevos de cover logic

---

## Operative Documents
- `/app/RUNBOOK.md` - Guía operativa (actualizada v1.1)
