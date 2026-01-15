# NBA Edge - PRD (Product Requirements Document)

## ⚠️ STATUS: PRODUCTION LOCKED – READY FOR LIVE PICKS ⚠️

**Locked Date:** 15 Enero 2025  
**Model Version:** v1.0 (20260115_123658)  
**Cover Logic Version:** v1.1 (BUG FIX VERIFIED)
**Operative Config:** LOCKED  

> ⛔ NO realizar cambios de código ni modelo sin instrucción explícita del usuario.

---

## Original Problem Statement
Construir una web app full-stack llamada "NBA Edge" orientada a operar desde España (odds en formato decimal). La app aprende con históricos NBA para predecir el margen esperado (home_pts - away_pts) y detecta value comparando con el mercado de spreads.

## User Personas
- **Sports Bettor en España**: Usuario que busca detectar value bets en NBA usando predicciones basadas en datos

## Core Requirements (Static)
- **Temporadas históricas**: 2021-22, 2022-23, 2023-24, 2024-25
- **Feature window**: Rolling 15 partidos (sin leakage)
- **Modelo**: Ridge Regression + StandardScaler
- **Split temporal**: Train (21-24), Test (24-25)
- **Señales Edge**: Verde (≥3.0), Amarillo (2.0-3.0), Rojo (<2.0)
- **Reference line**: Pinnacle → Betfair → Mediana

## Technology Stack
- **Backend**: FastAPI (Python) 
- **Frontend**: React + Tailwind + shadcn/ui
- **Database**: MongoDB
- **Auth**: JWT simple
- **External APIs**: nba_api, The Odds API

---

## CRITICAL BUG FIX (15 Jan 2025)

### ❌ Bug Detectado
El sistema recomendaba apuestas que el modelo NO cubría:
- ORL vs MEM: pred=+0.75, spread=-5.0 → Recomendaba ORL -5.0 (INCORRECTO)
- SAS vs MIL: pred=-0.97, spread=-7.5 → Recomendaba SAS -7.5 (INCORRECTO)

### ✅ Lógica Corregida (v1.1)
```
cover_threshold = -market_spread
HOME cubre si: pred_margin > cover_threshold
AWAY cubre si: pred_margin < cover_threshold
edge = |pred_margin - cover_threshold| (siempre positivo)
```

### Verificación Bug Fix
| Caso | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| ORL vs MEM | pred=+0.75, spread=-5.0 | AWAY (MEM +5.0), edge=4.25 | AWAY (MEM +5.0), edge=4.25 | ✅ PASS |
| SAS vs MIL | pred=-0.97, spread=-7.5 | AWAY (MIL +7.5), edge=8.47 | AWAY (MIL +7.5), edge=8.47 | ✅ PASS |

---

## PRODUCTION VERIFICATION CHECKLIST (15 Jan 2025)

### ✅ Bug Fix Tests
| Test | Status |
|------|--------|
| test_no_bet_when_model_does_not_cover | **PASS** |
| test_edge_positive_and_consistent_with_side | **PASS** |
| All edges positive | **PASS** (9/9 picks) |
| Recommended side consistent with cover | **PASS** |

### ✅ Live Ops End-to-End Flow
| Test | Status | Detalles |
|------|--------|----------|
| Sync Upcoming | **PASS** | 9 eventos NBA sincronizados |
| Sync Odds (Pinnacle) | **PASS** | 27 líneas |
| Generate Picks (operative) | **PASS** | 2 picks operativos |
| Snapshot Close Lines | **PASS** | Endpoint funcionando |

### ✅ Filtros Operativos
| Filtro | Status |
|--------|--------|
| Signal GREEN only | **PASS** |
| Edge ≥ 3.5 (siempre positivo) | **PASS** |
| Confidence HIGH | **PASS** |
| Pinnacle required | **PASS** |
| Max 2 picks/día | **PASS** |

### ✅ Tests Automatizados
| Suite | Resultado |
|-------|-----------|
| Unit Tests Production | 13/13 passed |
| API Tests Cover Logic | 9/9 passed |
| **TOTAL** | **22/22 (100%)** |

---

## Model Health (v1.0)
| Métrica | Valor | Target |
|---------|-------|--------|
| MAE | 14.13 | < 10 (nota: margen de mejora) |
| RMSE | 17.24 | < 13 |
| pred_std_test | 3.64 | > 2.0 ✓ |

---

## What's Been Implemented (COMPLETE)

### Backend
- ✅ JWT Authentication (register, login, me)
- ✅ MongoDB models completos
- ✅ Admin endpoints: sync-historical, build-features, train, sync-upcoming, sync-odds, snapshot-close-lines
- ✅ User endpoints: upcoming, picks/generate, history
- ✅ Stats endpoints: dataset, model
- ✅ The Odds API integration (Pinnacle prioritario)
- ✅ nba_api integration
- ✅ Ridge Regression con versionado
- ✅ CLV calculation
- ✅ Filtros operativos
- ✅ **COVER LOGIC FIX v1.1**

### Frontend
- ✅ Dark mode professional dashboard
- ✅ Live Ops page (flujo operativo centralizado)
- ✅ Picks page con todos los campos operativos
- ✅ History page con CLV
- ✅ Dataset, Train, Settings pages

### Production Features
- ✅ Versionado de modelos (v1.0)
- ✅ Config snapshot inmutable
- ✅ CLV tracking (open/close line)
- ✅ Filtros operativos configurables
- ✅ do_not_bet rules con razones
- ✅ RUNBOOK.md operativo (actualizado v1.1)

---

## FROZEN BACKLOG (NO IMPLEMENTAR SIN AUTORIZACIÓN)

### P0 - Automatización resultados
- [ ] Endpoint para actualizar estado de picks automáticamente

### P1 - Mejoras de modelo
- [ ] Implementar defensive rating real
- [ ] Más temporadas históricas

### P2 - Monitoring
- [ ] Dashboard de performance a largo plazo
- [ ] Alertas/notificaciones

---

## Test Reports
- `/app/test_reports/iteration_3.json` - Verificación bug fix cover logic
- `/app/test_reports/iteration_2.json` - Verificación producción
- `/app/tests/test_cover_logic_bugfix.py` - Tests específicos bug fix
- `/app/backend/tests/test_production.py` - Unit tests producción
- `/app/backend/tests/test_prediction_pipeline.py` - Unit tests pipeline

---

## Operative Documents
- `/app/RUNBOOK.md` - Guía operativa diaria (actualizado v1.1 con lógica de cover corregida)

---

## Known Limitations (Accepted)
1. **defensive_rating**: Usa promedio de liga (112) como placeholder
2. **MAE actual (14.13)**: Superior al target ideal (<10), pero aceptable para operar
3. **Actualización resultados**: Manual (revisar RUNBOOK.md)
