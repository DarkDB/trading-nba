# NBA Edge - PRD (Product Requirements Document)

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

## What's Been Implemented (15 Jan 2025)

### Backend
- ✅ JWT Authentication (register, login, me)
- ✅ MongoDB models (users, games, team_game_stats, game_features, models, upcoming_events, market_lines, predictions)
- ✅ Admin endpoints: sync-historical, build-features, train, sync-upcoming, sync-odds, refresh-results
- ✅ User endpoints: upcoming (with lines), picks/generate, picks, history, history/export
- ✅ Stats endpoints: dataset, model
- ✅ The Odds API integration (EU/UK regions, decimal odds)
- ✅ nba_api integration for historical data
- ✅ Ridge Regression model training with joblib persistence
- ✅ Reference line selection (Pinnacle → Betfair → Median)
- ✅ Edge calculation and signal classification

### Frontend
- ✅ Dark mode professional sports dashboard design
- ✅ Login/Register pages with basketball arena background
- ✅ Dashboard with stats overview and setup guidance
- ✅ Dataset page - sync historical data, build features
- ✅ Train page - train model, view metrics
- ✅ Upcoming page - games with bookmaker lines
- ✅ Picks page - generated predictions with signals
- ✅ History page - performance tracking with filters and CSV export
- ✅ Settings page - system configuration display

### Design
- Fonts: Barlow Condensed (headings), Manrope (body), JetBrains Mono (data)
- Colors: #09090B (background), #22C55E (primary/green), #EAB308 (warning/yellow), #EF4444 (destructive/red)
- Components: shadcn/ui with custom dark theme

## Prioritized Backlog

### P0 (Critical)
- [ ] Sincronizar datos históricos completos (actualmente limitado a 100 partidos por temporada para MVP)
- [ ] Integrar stats de equipo en tiempo real para predicciones más precisas

### P1 (High)
- [ ] Implementar refresh-results para actualizar resultados reales
- [ ] Añadir notificaciones cuando aparezcan picks de value (edge verde)
- [ ] Mejorar cálculo de defensive rating con stats de oponentes

### P2 (Medium)
- [ ] Añadir gráficos de rendimiento histórico
- [ ] Implementar filtros avanzados en History
- [ ] Añadir comparación de líneas entre bookmakers
- [ ] Modo manual de entrada de spreads (fallback)

### P3 (Nice to have)
- [ ] Push notifications para nuevos picks
- [ ] Integración con Telegram para alertas
- [ ] Análisis de ROI por umbral de edge

## Next Tasks
1. Ejecutar sincronización histórica completa con todas las temporadas
2. Construir features y entrenar modelo con datos reales
3. Generar picks para partidos actuales
4. Verificar precisión del modelo en temporada 2024-25
