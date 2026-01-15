import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import {
  Database,
  Brain,
  Calendar,
  Target,
  History,
  Settings,
  LogOut,
  TrendingUp
} from 'lucide-react';
import { Button } from '../ui/button';

const navItems = [
  { path: '/dashboard', icon: TrendingUp, label: 'Dashboard' },
  { path: '/dataset', icon: Database, label: 'Dataset' },
  { path: '/train', icon: Brain, label: 'Train' },
  { path: '/upcoming', icon: Calendar, label: 'Upcoming' },
  { path: '/picks', icon: Target, label: 'Picks' },
  { path: '/history', icon: History, label: 'History' },
  { path: '/settings', icon: Settings, label: 'Settings' },
];

export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <aside className="fixed left-0 top-0 h-full w-64 bg-card border-r border-border z-40 flex flex-col">
      {/* Logo */}
      <div className="p-6 border-b border-border">
        <h1 className="font-headings font-bold text-2xl tracking-tight text-white uppercase">
          NBA <span className="text-primary">Edge</span>
        </h1>
        <p className="text-xs text-zinc-500 mt-1">Value Betting Analytics</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 overflow-y-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-3 mx-2 rounded-md transition-colors ${
                isActive
                  ? 'bg-primary/10 text-primary'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
              }`
            }
            data-testid={`nav-${item.label.toLowerCase()}`}
          >
            <item.icon className="w-5 h-5" />
            <span className="font-body text-sm font-medium">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* User section */}
      <div className="p-4 border-t border-border">
        <div className="mb-3">
          <p className="text-sm font-medium text-white truncate">{user?.name}</p>
          <p className="text-xs text-zinc-500 truncate">{user?.email}</p>
        </div>
        <Button
          variant="ghost"
          className="w-full justify-start text-zinc-400 hover:text-white hover:bg-zinc-800"
          onClick={handleLogout}
          data-testid="logout-btn"
        >
          <LogOut className="w-4 h-4 mr-2" />
          Cerrar sesión
        </Button>
      </div>
    </aside>
  );
}
