"""
NBA Edge - Cover Logic Bug Fix Verification Tests
=================================================
CRITICAL: Verifies the bug fix for cover logic.

BUG DESCRIPTION:
- Old logic: pred_margin > market_spread → HOME
- This was WRONG because market_spread is negative for favorites

CORRECTED LOGIC:
- cover_threshold = -market_spread
- HOME covers if pred_margin > cover_threshold
- AWAY covers if pred_margin < cover_threshold
- Edge is always positive (distance from threshold)

BUG CASES FROM USER:
1. ORL vs MEM: pred=+0.75, spread=-5.0 → MUST be AWAY (MEM +5.0), edge=4.25
2. SAS vs MIL: pred=-0.97, spread=-7.5 → MUST be AWAY (MIL +7.5), edge=8.47
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://edge-trader-nba.preview.emergentagent.com').rstrip('/')


class TestCoverLogicBugFix:
    """Tests for the critical cover logic bug fix"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "test_liveops@nbaedge.com",
            "password": "TestPassword123!"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        self.token = response.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
    
    def test_api_health(self):
        """Verify API is healthy"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        print("✓ API health check passed")
    
    def test_generate_picks_returns_data(self):
        """Verify generate picks endpoint works"""
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "picks" in data
        assert len(data["picks"]) > 0
        print(f"✓ Generate picks returned {len(data['picks'])} picks")
    
    def test_bug_case_1_orl_vs_mem(self):
        """
        BUG CASE 1: ORL vs MEM
        pred_margin=+0.75, spread=-5.0
        
        OLD (WRONG): Would recommend HOME (ORL -5.0) because 0.75 > -5.0
        NEW (CORRECT): Recommends AWAY (MEM +5.0) because:
          - cover_threshold = -(-5.0) = 5.0
          - 0.75 < 5.0 → AWAY covers
          - edge = 5.0 - 0.75 = 4.25
        """
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.headers
        )
        assert response.status_code == 200
        picks = response.json()["picks"]
        
        # Find ORL vs MEM pick
        orl_pick = next((p for p in picks if p["home_team"] == "Orlando Magic"), None)
        
        if orl_pick:
            print(f"\nORL vs MEM Pick:")
            print(f"  pred_margin: {orl_pick['pred_margin']}")
            print(f"  open_spread: {orl_pick['open_spread']}")
            print(f"  recommended_side: {orl_pick['recommended_side']}")
            print(f"  recommended_bet_string: {orl_pick['recommended_bet_string']}")
            print(f"  edge_points: {orl_pick['edge_points']}")
            
            # Verify the fix
            assert orl_pick["recommended_side"] == "AWAY", \
                f"BUG NOT FIXED: Expected AWAY, got {orl_pick['recommended_side']}"
            assert "MEM" in orl_pick["recommended_bet_string"], \
                f"BUG NOT FIXED: Expected MEM in bet string, got {orl_pick['recommended_bet_string']}"
            assert orl_pick["edge_points"] > 0, \
                f"Edge must be positive, got {orl_pick['edge_points']}"
            
            # Verify edge calculation: 5.0 - 0.75 = 4.25
            expected_edge = 4.25
            assert abs(orl_pick["edge_points"] - expected_edge) < 0.5, \
                f"Edge should be ~{expected_edge}, got {orl_pick['edge_points']}"
            
            print("✓ BUG CASE 1 (ORL vs MEM) VERIFIED - CORRECT!")
        else:
            pytest.skip("ORL vs MEM game not in current schedule")
    
    def test_bug_case_2_sas_vs_mil(self):
        """
        BUG CASE 2: SAS vs MIL
        pred_margin=-0.97, spread=-7.5
        
        OLD (WRONG): Would recommend HOME (SAS -7.5) because -0.97 > -7.5
        NEW (CORRECT): Recommends AWAY (MIL +7.5) because:
          - cover_threshold = -(-7.5) = 7.5
          - -0.97 < 7.5 → AWAY covers
          - edge = 7.5 - (-0.97) = 8.47
        """
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.headers
        )
        assert response.status_code == 200
        picks = response.json()["picks"]
        
        # Find SAS vs MIL pick
        sas_pick = next((p for p in picks if p["home_team"] == "San Antonio Spurs"), None)
        
        if sas_pick:
            print(f"\nSAS vs MIL Pick:")
            print(f"  pred_margin: {sas_pick['pred_margin']}")
            print(f"  open_spread: {sas_pick['open_spread']}")
            print(f"  recommended_side: {sas_pick['recommended_side']}")
            print(f"  recommended_bet_string: {sas_pick['recommended_bet_string']}")
            print(f"  edge_points: {sas_pick['edge_points']}")
            
            # Verify the fix
            assert sas_pick["recommended_side"] == "AWAY", \
                f"BUG NOT FIXED: Expected AWAY, got {sas_pick['recommended_side']}"
            assert "MIL" in sas_pick["recommended_bet_string"], \
                f"BUG NOT FIXED: Expected MIL in bet string, got {sas_pick['recommended_bet_string']}"
            assert sas_pick["edge_points"] > 0, \
                f"Edge must be positive, got {sas_pick['edge_points']}"
            
            # Verify edge calculation: 7.5 - (-0.97) = 8.47
            expected_edge = 8.47
            assert abs(sas_pick["edge_points"] - expected_edge) < 0.5, \
                f"Edge should be ~{expected_edge}, got {sas_pick['edge_points']}"
            
            print("✓ BUG CASE 2 (SAS vs MIL) VERIFIED - CORRECT!")
        else:
            pytest.skip("SAS vs MIL game not in current schedule")
    
    def test_all_edges_are_positive(self):
        """Verify ALL edges are positive (never negative)"""
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.headers
        )
        assert response.status_code == 200
        picks = response.json()["picks"]
        
        negative_edges = []
        for pick in picks:
            if pick["edge_points"] < 0:
                negative_edges.append({
                    "matchup": f"{pick['home_team']} vs {pick['away_team']}",
                    "edge": pick["edge_points"]
                })
        
        assert len(negative_edges) == 0, \
            f"Found negative edges (BUG!): {negative_edges}"
        
        print(f"✓ All {len(picks)} picks have positive edges")
    
    def test_recommended_side_consistent_with_cover(self):
        """
        Verify recommended_side is consistent with cover logic:
        - If pred_margin > cover_threshold → HOME
        - If pred_margin < cover_threshold → AWAY
        """
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.headers
        )
        assert response.status_code == 200
        picks = response.json()["picks"]
        
        inconsistent = []
        for pick in picks:
            pred_margin = pick["pred_margin"]
            market_spread = pick["open_spread"]
            recommended_side = pick["recommended_side"]
            
            # Calculate expected side
            cover_threshold = -market_spread
            expected_side = "HOME" if pred_margin > cover_threshold else "AWAY"
            
            if recommended_side != expected_side:
                inconsistent.append({
                    "matchup": f"{pick['home_team']} vs {pick['away_team']}",
                    "pred_margin": pred_margin,
                    "market_spread": market_spread,
                    "cover_threshold": cover_threshold,
                    "expected": expected_side,
                    "got": recommended_side
                })
        
        assert len(inconsistent) == 0, \
            f"Found inconsistent recommendations (BUG!): {inconsistent}"
        
        print(f"✓ All {len(picks)} picks have consistent recommended_side")
    
    def test_bet_string_matches_side(self):
        """
        Verify recommended_bet_string matches recommended_side:
        - HOME → home_abbr with market_spread
        - AWAY → away_abbr with -market_spread
        """
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=false",
            headers=self.headers
        )
        assert response.status_code == 200
        picks = response.json()["picks"]
        
        mismatches = []
        for pick in picks:
            bet_string = pick["recommended_bet_string"]
            side = pick["recommended_side"]
            home_abbr = pick.get("home_abbr", "")
            away_abbr = pick.get("away_abbr", "")
            
            if side == "HOME" and home_abbr and home_abbr not in bet_string:
                mismatches.append({
                    "matchup": f"{pick['home_team']} vs {pick['away_team']}",
                    "side": side,
                    "bet_string": bet_string,
                    "expected_team": home_abbr
                })
            elif side == "AWAY" and away_abbr and away_abbr not in bet_string:
                mismatches.append({
                    "matchup": f"{pick['home_team']} vs {pick['away_team']}",
                    "side": side,
                    "bet_string": bet_string,
                    "expected_team": away_abbr
                })
        
        assert len(mismatches) == 0, \
            f"Found bet string mismatches: {mismatches}"
        
        print(f"✓ All {len(picks)} picks have correct bet strings")
    
    def test_operative_filters_working(self):
        """Verify operative filters are applied correctly"""
        response = requests.post(
            f"{BASE_URL}/api/picks/generate?operative_mode=true",
            headers=self.headers
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data["operative_mode"] == True
        assert "filters_applied" in data
        
        # Check all operative picks meet criteria
        for pick in data["picks"]:
            assert pick["signal"] == "green", \
                f"Operative pick has non-green signal: {pick['signal']}"
            assert pick["edge_points"] >= 3.5, \
                f"Operative pick has edge < 3.5: {pick['edge_points']}"
            assert pick["confidence"] == "high", \
                f"Operative pick has non-high confidence: {pick['confidence']}"
            assert pick["do_not_bet"] == False, \
                f"Operative pick has do_not_bet=True"
        
        # Check max picks limit
        assert len(data["picks"]) <= 2, \
            f"More than max_picks_per_day (2): {len(data['picks'])}"
        
        print(f"✓ Operative filters working: {len(data['picks'])} picks from {data['total_analyzed']} analyzed")
    
    def test_debug_endpoint_consistent(self):
        """Verify debug endpoint uses same corrected logic"""
        # Get upcoming events
        response = requests.get(f"{BASE_URL}/api/upcoming", headers=self.headers)
        assert response.status_code == 200
        events = response.json()["events"]
        
        if not events:
            pytest.skip("No upcoming events")
        
        # Test first event
        event_id = events[0]["event_id"]
        response = requests.get(
            f"{BASE_URL}/api/admin/debug/predict?event_id={event_id}",
            headers=self.headers
        )
        assert response.status_code == 200
        debug = response.json()
        
        # Verify edge is positive
        if debug.get("edge_points") is not None:
            assert debug["edge_points"] >= 0, \
                f"Debug endpoint has negative edge: {debug['edge_points']}"
        
        # Verify side is consistent with cover logic
        if debug.get("market_spread") is not None and debug.get("recommended_side"):
            cover_threshold = -debug["market_spread"]
            expected_side = "HOME" if debug["pred_margin"] > cover_threshold else "AWAY"
            assert debug["recommended_side"] == expected_side, \
                f"Debug endpoint side mismatch: expected {expected_side}, got {debug['recommended_side']}"
        
        print(f"✓ Debug endpoint uses corrected cover logic")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
