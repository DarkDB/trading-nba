import "@/App.css";
import "@/index.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { Toaster } from "./components/ui/sonner";

// Pages
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Dataset from "./pages/Dataset";
import Train from "./pages/Train";
import Upcoming from "./pages/Upcoming";
import Picks from "./pages/Picks";
import History from "./pages/History";
import Settings from "./pages/Settings";
import LiveOps from "./pages/LiveOps";
import OpsDashboard from "./pages/OpsDashboard";

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public routes */}
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          
          {/* Protected routes */}
          <Route element={<DashboardLayout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/live-ops" element={<LiveOps />} />
            <Route path="/ops-dashboard" element={<OpsDashboard />} />
            <Route path="/dataset" element={<Dataset />} />
            <Route path="/train" element={<Train />} />
            <Route path="/upcoming" element={<Upcoming />} />
            <Route path="/picks" element={<Picks />} />
            <Route path="/history" element={<History />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
          
          {/* Redirect root to dashboard */}
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </AuthProvider>
  );
}

export default App;
