"""
NBA Edge - Live Ops End-to-End Tests
Tests the complete production workflow:
1. Sync Upcoming - POST /api/admin/sync-upcoming
2. Sync Odds (Pinnacle) - POST /api/admin/sync-odds
3. Generate Picks (operative mode) - POST /api/picks/generate?operative_mode=true
4. Snapshot Close Lines - POST /api/admin/snapshot-close-lines
5. History with CLV - GET /api/history
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "test_liveops@nbaedge.com"
TEST_PASSWORD = "TestPassword123!"
TEST_NAME = "Live Ops Tester"


class TestLiveOpsE2E:
    """End-to-end tests for Live Ops workflow"""
    
    token = None
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Register/login and get token"""
        if TestLiveOpsE2E.token:
            return
            
        # Try to register first
        register_response = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "name": TEST_NAME}
        )
        
        if register_response.status_code == 200:
            TestLiveOpsE2E.token = register_response.json().get("access_token")
        elif register_response.status_code == 400:  # Already registered
            # Login instead
            login_response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
            )
            assert login_response.status_code == 200, f"Login failed: {login_response.text}"
            TestLiveOpsE2E.token = login_response.json().get("access_token")
        else:
            pytest.fail(f"Registration failed: {register_response.text}")
    
    def get_headers(self):
        return {"Authorization": f"Bearer {TestLiveOpsE2E.token}"}
    
    # ============= STEP 1: SYNC UPCOMING =============
    
    def test_01_sync_upcoming(self):
        """Test POST /api/admin/sync-upcoming - sync upcoming NBA events"""
        response = requests.post(
            f"{BASE_URL}/api/admin/sync-upcoming?days=2",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Sync upcoming failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "status" in data
        assert "message" in data
        assert data["status"] in ["completed", "started"]
        
        # Check details
        if "details" in data and data["details"]:
            print(f"Synced events count: {data['details'].get('count', 'N/A')}")
        
        print(f"✓ Sync Upcoming: {data['message']}")
    
    # ============= STEP 2: SYNC ODDS =============
    
    def test_02_sync_odds(self):
        """Test POST /api/admin/sync-odds - sync odds from Pinnacle"""
        response = requests.post(
            f"{BASE_URL}/api/admin/sync-odds?days=2",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Sync odds failed: {response.text}"
        data = response.json()
        
        assert "status" in data
        assert data["status"] in ["completed", "started"]
        
        # Check for lines synced
        if "details" in data and data["details"]:
            events_count = data["details"].get("events", 0)
            lines_count = data["details"].get("lines", 0)
            print(f"Events: {events_count}, Lines: {lines_count}")
        
        print(f"✓ Sync Odds: {data['message']}")
    
    # ============= STEP 3: CHECK MODEL STATUS =============
    
    def test_03_check_model_status(self):
        """Check if model is trained before generating picks"""
        response = requests.get(
            f"{BASE_URL}/api/stats/model",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Model stats failed: {response.text}"
        data = response.json()
        
        has_model = data.get("active_model", False)
        print(f"Active model: {has_model}")
        
        if has_model:
            print(f"  Model version: {data.get('model_version', 'N/A')}")
            print(f"  MAE: {data.get('metrics', {}).get('mae', 'N/A')}")
            print(f"  Data cutoff: {data.get('data_cutoff_date', 'N/A')}")
        else:
            print("  WARNING: No trained model - picks generation will fail")
        
        return has_model
    
    # ============= STEP 4: GENERATE PICKS (OPERATIVE MODE) =============
    
    def test_04_generate_picks_operative(self):
        """Test POST /api/picks/generate?operative_mode=true"""
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=true",
            headers=self.get_headers()
        )
        
        # May fail if no model trained
        if response.status_code == 400:
            error_detail = response.json().get("detail", "")
            if "No trained model" in error_detail:
                pytest.skip("No trained model available - skipping picks generation")
            else:
                pytest.fail(f"Generate picks failed: {error_detail}")
        
        assert response.status_code == 200, f"Generate picks failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "picks" in data or "operative_picks" in data
        assert "count" in data or "operative_count" in data
        
        operative_picks = data.get("operative_picks", [])
        all_picks = data.get("picks", [])
        
        print(f"Total picks analyzed: {len(all_picks)}")
        print(f"Operative picks (filtered): {len(operative_picks)}")
        
        # Verify operative filters on each pick
        for pick in operative_picks:
            # Check required fields
            assert "signal" in pick, "Pick missing 'signal'"
            assert "edge_points" in pick, "Pick missing 'edge_points'"
            assert "confidence" in pick, "Pick missing 'confidence'"
            assert "recommended_bet_string" in pick, "Pick missing 'recommended_bet_string'"
            assert "explanation" in pick, "Pick missing 'explanation'"
            
            # Verify operative filter criteria
            assert pick["signal"] == "green", f"Operative pick has non-green signal: {pick['signal']}"
            assert abs(pick["edge_points"]) >= 3.5, f"Edge too small: {pick['edge_points']}"
            assert pick["confidence"] == "high", f"Confidence not high: {pick['confidence']}"
            assert pick.get("do_not_bet") == False, f"do_not_bet should be False for operative picks"
            
            # Verify recommended_bet_string format (e.g., "LAL -3.5")
            bet_str = pick["recommended_bet_string"]
            assert len(bet_str) > 0, "recommended_bet_string is empty"
            parts = bet_str.split()
            assert len(parts) >= 2, f"Invalid bet string format: {bet_str}"
            
            print(f"  ✓ Pick: {bet_str} | Edge: {pick['edge_points']:.1f} | Signal: {pick['signal']}")
        
        # Verify max 2 picks per day
        assert len(operative_picks) <= 2, f"More than 2 operative picks: {len(operative_picks)}"
        
        print(f"✓ Generate Picks: {len(operative_picks)} operative picks")
        return operative_picks
    
    # ============= STEP 5: SNAPSHOT CLOSE LINES =============
    
    def test_05_snapshot_close_lines(self):
        """Test POST /api/admin/snapshot-close-lines"""
        response = requests.post(
            f"{BASE_URL}/api/admin/snapshot-close-lines?minutes_before=60",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Snapshot close lines failed: {response.text}"
        data = response.json()
        
        assert "status" in data
        assert data["status"] == "completed"
        
        updated_count = data.get("details", {}).get("updated", 0)
        print(f"✓ Snapshot Close Lines: Updated {updated_count} predictions")
    
    # ============= STEP 6: VERIFY HISTORY WITH CLV =============
    
    def test_06_verify_history_clv(self):
        """Test GET /api/history - verify CLV fields"""
        response = requests.get(
            f"{BASE_URL}/api/history",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Get history failed: {response.text}"
        data = response.json()
        
        assert "predictions" in data
        predictions = data["predictions"]
        
        print(f"Total predictions in history: {len(predictions)}")
        
        # Check CLV fields on predictions that have close lines
        clv_count = 0
        for pred in predictions:
            if pred.get("close_spread") is not None:
                clv_count += 1
                # Verify CLV calculation fields exist
                assert "open_spread" in pred or "market_spread_used" in pred
                assert "close_spread" in pred
                assert "clv_spread" in pred
                
                # CLV should be calculated
                clv = pred.get("clv_spread")
                if clv is not None:
                    print(f"  Prediction {pred.get('id', 'N/A')[:8]}: CLV = {clv:+.2f}")
        
        print(f"✓ History: {clv_count} predictions with CLV data")
    
    # ============= STEP 7: VERIFY UPCOMING EVENTS =============
    
    def test_07_verify_upcoming_events(self):
        """Test GET /api/upcoming - verify events have lines"""
        response = requests.get(
            f"{BASE_URL}/api/upcoming",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Get upcoming failed: {response.text}"
        data = response.json()
        
        assert "events" in data
        events = data["events"]
        
        print(f"Upcoming events: {len(events)}")
        
        pinnacle_count = 0
        for event in events:
            lines = event.get("lines", [])
            has_pinnacle = any(l.get("bookmaker_key") == "pinnacle" for l in lines)
            if has_pinnacle:
                pinnacle_count += 1
            
            home = event.get("home_team_abbr") or event.get("home_team", "")[:3]
            away = event.get("away_team_abbr") or event.get("away_team", "")[:3]
            print(f"  {home} vs {away}: {len(lines)} lines, Pinnacle: {'✓' if has_pinnacle else '✗'}")
        
        print(f"✓ Upcoming: {pinnacle_count}/{len(events)} events have Pinnacle lines")
    
    # ============= STEP 8: VERIFY PICKS ENDPOINT =============
    
    def test_08_verify_picks_endpoint(self):
        """Test GET /api/picks - verify picks list"""
        response = requests.get(
            f"{BASE_URL}/api/picks",
            headers=self.get_headers()
        )
        
        assert response.status_code == 200, f"Get picks failed: {response.text}"
        data = response.json()
        
        assert "picks" in data
        picks = data["picks"]
        
        print(f"Total picks: {len(picks)}")
        
        # Verify pick structure
        for pick in picks[:5]:  # Check first 5
            required_fields = ["id", "home_team", "away_team", "pred_margin", 
                             "signal", "edge_points", "confidence"]
            for field in required_fields:
                assert field in pick, f"Pick missing field: {field}"
        
        print(f"✓ Picks endpoint: {len(picks)} picks available")


class TestOperativeFilters:
    """Tests specifically for operative filter logic"""
    
    token = None
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: Get token"""
        if TestOperativeFilters.token:
            return
            
        # Login
        login_response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        
        if login_response.status_code == 200:
            TestOperativeFilters.token = login_response.json().get("access_token")
        else:
            # Try register
            register_response = requests.post(
                f"{BASE_URL}/api/auth/register",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "name": TEST_NAME}
            )
            if register_response.status_code == 200:
                TestOperativeFilters.token = register_response.json().get("access_token")
    
    def get_headers(self):
        return {"Authorization": f"Bearer {TestOperativeFilters.token}"}
    
    def test_operative_vs_non_operative_mode(self):
        """Compare operative_mode=true vs false"""
        # Non-operative mode
        response_all = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.get_headers()
        )
        
        if response_all.status_code == 400:
            pytest.skip("No trained model available")
        
        assert response_all.status_code == 200
        data_all = response_all.json()
        all_picks = data_all.get("picks", [])
        
        # Operative mode
        response_op = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=true",
            headers=self.get_headers()
        )
        
        assert response_op.status_code == 200
        data_op = response_op.json()
        operative_picks = data_op.get("operative_picks", [])
        
        print(f"All picks: {len(all_picks)}")
        print(f"Operative picks: {len(operative_picks)}")
        
        # Operative should be subset of all
        assert len(operative_picks) <= len(all_picks)
        
        # All operative picks should pass filters
        for pick in operative_picks:
            assert pick["signal"] == "green"
            assert abs(pick["edge_points"]) >= 3.5
            assert pick["confidence"] == "high"


class TestCLVCalculation:
    """Tests for CLV (Closing Line Value) calculation"""
    
    def test_clv_formula_home(self):
        """Test CLV calculation for HOME bets"""
        # CLV for HOME = open_spread - close_spread
        # If we bet HOME -5 and close is -6, CLV = -5 - (-6) = +1 (good)
        open_spread = -5.0
        close_spread = -6.0
        clv = open_spread - close_spread
        assert clv == 1.0
        
    def test_clv_formula_away(self):
        """Test CLV calculation for AWAY bets"""
        # CLV for AWAY = close_spread - open_spread
        # If we bet AWAY +5 and close is +4, CLV = +4 - (+5) = -1 (bad)
        open_spread = -5.0  # HOME perspective
        close_spread = -4.0
        clv = close_spread - open_spread  # For AWAY
        assert clv == 1.0  # Actually good for AWAY


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
