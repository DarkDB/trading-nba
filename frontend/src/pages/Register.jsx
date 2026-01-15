import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { toast } from 'sonner';
import { Loader2, TrendingUp } from 'lucide-react';

export default function Register() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      await register(email, password, name);
      toast.success('Cuenta creada correctamente');
      navigate('/dashboard');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error al crear la cuenta');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div 
      className="min-h-screen flex items-center justify-center p-4 relative"
      style={{
        backgroundImage: `linear-gradient(to bottom, rgba(9, 9, 11, 0.8), rgba(9, 9, 11, 0.95)), url('https://images.unsplash.com/photo-1558453975-d6c57ab62b20?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NDk1Nzd8MHwxfHNlYXJjaHwxfHxuYmElMjBiYXNrZXRiYWxsJTIwZ2FtZSUyMGFjdGlvbiUyMGRhcmslMjBhcmVuYXxlbnwwfHx8fDE3Njg0NzY4NDV8MA&ixlib=rb-4.1.0&q=85')`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      <Card className="w-full max-w-md bg-card/95 backdrop-blur-sm border-border">
        <CardHeader className="text-center">
          <div className="flex items-center justify-center gap-2 mb-2">
            <TrendingUp className="w-8 h-8 text-primary" />
            <h1 className="font-headings font-bold text-3xl tracking-tight text-white uppercase">
              NBA <span className="text-primary">Edge</span>
            </h1>
          </div>
          <CardTitle className="font-headings text-xl text-zinc-100">Crear Cuenta</CardTitle>
          <CardDescription className="text-zinc-400">
            Regístrate para acceder a predicciones NBA
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name" className="text-zinc-300">Nombre</Label>
              <Input
                id="name"
                type="text"
                placeholder="Tu nombre"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                className="bg-zinc-900 border-zinc-700 text-white placeholder:text-zinc-500"
                data-testid="register-name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email" className="text-zinc-300">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="tu@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="bg-zinc-900 border-zinc-700 text-white placeholder:text-zinc-500"
                data-testid="register-email"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password" className="text-zinc-300">Contraseña</Label>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                className="bg-zinc-900 border-zinc-700 text-white placeholder:text-zinc-500"
                data-testid="register-password"
              />
            </div>
            <Button
              type="submit"
              className="w-full bg-primary hover:bg-primary/90 text-white font-headings font-bold uppercase tracking-wide"
              disabled={loading}
              data-testid="register-submit"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              ) : null}
              Crear Cuenta
            </Button>
          </form>
          <p className="text-center text-sm text-zinc-500 mt-4">
            ¿Ya tienes cuenta?{' '}
            <Link to="/login" className="text-primary hover:underline">
              Inicia sesión
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
