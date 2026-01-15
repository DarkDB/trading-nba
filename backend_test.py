#!/usr/bin/env python3
"""
NBA Edge Backend API Testing Suite
Tests all backend endpoints for the NBA betting predictions app
"""

import requests
import sys
import json
from datetime import datetime

class NBAEdgeAPITester:
    def __init__(self, base_url="https://nba-forecast-3.preview.emergentagent.com"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, success, details=None):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
            print(f"✅ {name} - PASSED")
        else:
            print(f"❌ {name} - FAILED")
            if details:
                print(f"   Details: {details}")
        
        self.test_results.append({
            "test": name,
            "success": success,
            "details": details
        })

    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None):
        """Run a single API test"""
        url = f"{self.base_url}/api/{endpoint}"
        test_headers = {'Content-Type': 'application/json'}
        
        if self.token:
            test_headers['Authorization'] = f'Bearer {self.token}'
        
        if headers:
            test_headers.update(headers)

        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=test_headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=test_headers, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=test_headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=test_headers, timeout=30)

            success = response.status_code == expected_status
            
            if success:
                self.log_test(name, True, f"Status: {response.status_code}")
                try:
                    return True, response.json()
                except:
                    return True, response.text
            else:
                error_msg = f"Expected {expected_status}, got {response.status_code}"
                try:
                    error_detail = response.json()
                    error_msg += f" - {error_detail}"
                except:
                    error_msg += f" - {response.text[:200]}"
                
                self.log_test(name, False, error_msg)
                return False, {}

        except Exception as e:
            self.log_test(name, False, f"Exception: {str(e)}")
            return False, {}

    def test_health_check(self):
        """Test basic health endpoint"""
        return self.run_test("Health Check", "GET", "health", 200)

    def test_root_endpoint(self):
        """Test root API endpoint"""
        return self.run_test("Root Endpoint", "GET", "", 200)

    def test_login_existing_user(self):
        """Test login with existing test user"""
        success, response = self.run_test(
            "Login Existing User",
            "POST",
            "auth/login",
            200,
            data={"email": "test@nbaedge.com", "password": "test123"}
        )
        
        if success and 'access_token' in response:
            self.token = response['access_token']
            print(f"   ✓ Token obtained: {self.token[:20]}...")
            return True, response
        return False, {}

    def test_register_new_user(self):
        """Test user registration with new user"""
        timestamp = datetime.now().strftime("%H%M%S")
        test_email = f"testuser_{timestamp}@nbaedge.com"
        
        success, response = self.run_test(
            "Register New User",
            "POST",
            "auth/register",
            200,
            data={
                "email": test_email,
                "password": "TestPass123!",
                "name": f"Test User {timestamp}"
            }
        )
        return success, response

    def test_get_current_user(self):
        """Test getting current user info"""
        if not self.token:
            self.log_test("Get Current User", False, "No token available")
            return False, {}
        
        return self.run_test("Get Current User", "GET", "auth/me", 200)

    def test_dataset_stats(self):
        """Test dataset statistics endpoint"""
        if not self.token:
            self.log_test("Dataset Stats", False, "No token available")
            return False, {}
        
        return self.run_test("Dataset Stats", "GET", "stats/dataset", 200)

    def test_model_stats(self):
        """Test model statistics endpoint"""
        if not self.token:
            self.log_test("Model Stats", False, "No token available")
            return False, {}
        
        return self.run_test("Model Stats", "GET", "stats/model", 200)

    def test_sync_upcoming_events(self):
        """Test syncing upcoming NBA events"""
        if not self.token:
            self.log_test("Sync Upcoming Events", False, "No token available")
            return False, {}
        
        return self.run_test("Sync Upcoming Events", "POST", "admin/sync-upcoming?days=2", 200)

    def test_sync_odds(self):
        """Test syncing odds from The Odds API"""
        if not self.token:
            self.log_test("Sync Odds", False, "No token available")
            return False, {}
        
        return self.run_test("Sync Odds", "POST", "admin/sync-odds?days=2", 200)

    def test_get_upcoming_events(self):
        """Test getting upcoming events with lines"""
        if not self.token:
            self.log_test("Get Upcoming Events", False, "No token available")
            return False, {}
        
        return self.run_test("Get Upcoming Events", "GET", "upcoming", 200)

    def test_generate_picks(self):
        """Test generating picks (may fail if no model trained)"""
        if not self.token:
            self.log_test("Generate Picks", False, "No token available")
            return False, {}
        
        # This might return 400 if no model is trained, which is acceptable
        success, response = self.run_test("Generate Picks", "POST", "picks/generate", 200)
        if not success:
            # Check if it's a model-related error (acceptable)
            if "No trained model available" in str(response):
                self.log_test("Generate Picks (No Model)", True, "No model trained yet - expected")
                return True, response
        return success, response

    def test_get_picks(self):
        """Test getting user picks"""
        if not self.token:
            self.log_test("Get Picks", False, "No token available")
            return False, {}
        
        return self.run_test("Get Picks", "GET", "picks", 200)

    def run_all_tests(self):
        """Run comprehensive test suite"""
        print("=" * 60)
        print("NBA EDGE BACKEND API TEST SUITE")
        print("=" * 60)
        
        # Basic connectivity tests
        print("\n📡 CONNECTIVITY TESTS")
        self.test_health_check()
        self.test_root_endpoint()
        
        # Authentication tests
        print("\n🔐 AUTHENTICATION TESTS")
        self.test_login_existing_user()
        self.test_register_new_user()
        self.test_get_current_user()
        
        # Stats endpoints
        print("\n📊 STATISTICS TESTS")
        self.test_dataset_stats()
        self.test_model_stats()
        
        # Admin endpoints
        print("\n⚙️ ADMIN TESTS")
        self.test_sync_upcoming_events()
        self.test_sync_odds()
        
        # User endpoints
        print("\n👤 USER TESTS")
        self.test_get_upcoming_events()
        self.test_generate_picks()
        self.test_get_picks()
        
        # Print summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"Tests Run: {self.tests_run}")
        print(f"Tests Passed: {self.tests_passed}")
        print(f"Tests Failed: {self.tests_run - self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed / self.tests_run * 100):.1f}%")
        
        # Return results for further processing
        return {
            "total": self.tests_run,
            "passed": self.tests_passed,
            "failed": self.tests_run - self.tests_passed,
            "success_rate": (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0,
            "results": self.test_results
        }

def main():
    """Main test execution"""
    tester = NBAEdgeAPITester()
    results = tester.run_all_tests()
    
    # Exit with error code if tests failed
    if results["failed"] > 0:
        print(f"\n❌ {results['failed']} tests failed")
        return 1
    else:
        print(f"\n✅ All {results['passed']} tests passed!")
        return 0

if __name__ == "__main__":
    sys.exit(main())