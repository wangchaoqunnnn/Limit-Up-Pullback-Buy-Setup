import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/tokens.css';
import './styles/global.css';
import './styles/components.css';

const container = document.getElementById('root');

if (!container) {
  throw new Error('未找到根节点 #root，无法挂载应用');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
