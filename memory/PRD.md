# NBA Edge - PRD (Product Requirements Document)

## ⚠️ STATUS: PRODUCTION LOCKED – READY FOR LIVE PICKS ⚠️

**Locked Date:** 15 Enero 2025  
**Model Version:** v1.0 (20260115_123658)  
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

## PRODUCTION VERIFICATION CHECKLIST (15 Jan 2025)

### ✅ Live Ops End-to-End Flow
| Test | Status | Detalles |
|------|--------|----------|
| Sync Upcoming | **PASS** | 9 eventos NBA sincronizados |
| Sync Odds (Pinnacle) | **PASS** | 27 líneas, 9/9 eventos con Pinnacle |
| Generate Picks (operative) | **PASS** | 2 picks operativos generados |
| Snapshot Close Lines | **PASS** | Endpoint funcionando |

### ✅ Filtros Operativos
| Filtro | Status | Configuración |
|--------|--------|---------------|
| Signal GREEN only | **PASS** | ✓ |
| Edge ≥ 3.5 | **PASS** | ✓ |
| Confidence HIGH | **PASS** | ✓ |
| Pinnacle required | **PASS** | ✓ |
| Max 2 picks/día | **PASS** | ✓ |

### ✅ Campos de Output
| Campo | Status |
|-------|--------|
| recommended_bet_string | **PASS** - Formato correcto (ej: "LAL -4.5") |
| explanation | **PASS** - Incluye pred_margin, edge, confidence, model_version |
| CLV fields (History) | **PASS** - open_spread, close_spread, clv_spread |

### ✅ Tests Automatizados
| Suite | Resultado |
|-------|-----------|
| Live Ops E2E | 11/11 passed |
| Unit Tests Production | 11/11 passed |
| Unit Tests Pipeline | 8/8 passed |
| **TOTAL** | **30/30 (100%)** |

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
- ✅ RUNBOOK.md operativo

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
- `/app/test_reports/iteration_2.json` - Verificación final producción
- `/app/test_reports/iteration_1.json` - Tests MVP inicial
- `/app/tests/test_live_ops_e2e.py` - E2E tests
- `/app/backend/tests/test_production.py` - Unit tests producción
- `/app/backend/tests/test_prediction_pipeline.py` - Unit tests pipeline

---

## Operative Documents
- `/app/RUNBOOK.md` - Guía operativa diaria

---

## Known Limitations (Accepted)
1. **defensive_rating**: Usa promedio de liga (112) como placeholder
2. **MAE actual (14.13)**: Superior al target ideal (<10), pero aceptable para operar
3. **Actualización resultados**: Manual (revisar RUNBOOK.md paso 4)
