import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';

export default function Layout({ children }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [user, setUser] = useState(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token && loc.pathname !== '/login') {
      nav('/login');
      return;
    }
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      setUser({ email: payload.sub });
    } catch {
      setUser(null);
    }
  }, [loc.pathname, nav]);

  const logout = () => {
    localStorage.removeItem('token');
    nav('/login');
  };

  const navLink = (to, label) => {
    const active = loc.pathname === to;
    return (
      <Link
        key={to}
        to={to}
        className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
          active
            ? 'bg-blue-600 text-white'
            : 'text-gray-700 hover:bg-gray-100'
        }`}
      >
        {label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold">
                L
              </div>
              <span className="text-lg font-bold text-gray-900">Locium</span>
            </div>
            <nav className="flex items-center gap-2">
              {navLink('/', 'Дашборд')}
              {navLink('/products', 'Товары')}
            </nav>
            <div className="flex items-center gap-3">
              {user && (
                <span className="text-sm text-gray-500 hidden sm:inline">
                  {user.email}
                </span>
              )}
              <button
                onClick={logout}
                className="text-sm text-red-600 hover:text-red-700 font-medium"
              >
                Выйти
              </button>
            </div>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>
    </div>
  );
}