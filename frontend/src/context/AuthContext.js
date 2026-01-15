import React, { createContext, useContext, useState, useEffect } from 'react';
import { authApi } from '../lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('nba_edge_token');
    const savedUser = localStorage.getItem('nba_edge_user');
    
    if (token && savedUser) {
      setUser(JSON.parse(savedUser));
      // Verify token is still valid
      authApi.me()
        .then((res) => {
          setUser(res.data);
          localStorage.setItem('nba_edge_user', JSON.stringify(res.data));
        })
        .catch(() => {
          logout();
        })
        .finally(() => {
          setLoading(false);
        });
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (email, password) => {
    const response = await authApi.login({ email, password });
    const { access_token, user } = response.data;
    
    localStorage.setItem('nba_edge_token', access_token);
    localStorage.setItem('nba_edge_user', JSON.stringify(user));
    setUser(user);
    
    return user;
  };

  const register = async (email, password, name) => {
    const response = await authApi.register({ email, password, name });
    const { access_token, user } = response.data;
    
    localStorage.setItem('nba_edge_token', access_token);
    localStorage.setItem('nba_edge_user', JSON.stringify(user));
    setUser(user);
    
    return user;
  };

  const logout = () => {
    localStorage.removeItem('nba_edge_token');
    localStorage.removeItem('nba_edge_user');
    setUser(null);
  };

  const value = {
    user,
    loading,
    login,
    register,
    logout,
    isAuthenticated: !!user,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
