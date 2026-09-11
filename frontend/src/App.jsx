import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Products from './pages/Products';
import Shops from './pages/Shops';
import Reports from './pages/Reports';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <Layout>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/products" element={<Products />} />
              <Route path="/shops" element={<Shops />} />
              <Route path="/reports" element={<Reports />} />
            </Routes>
          </Layout>
        }
      />
    </Routes>
  );
}