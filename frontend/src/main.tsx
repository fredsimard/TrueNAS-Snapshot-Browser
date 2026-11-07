import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from '@/App'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import '@/index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TooltipProvider delayDuration={400}>
      <App />
      <Toaster position="bottom-right" offset={{ bottom: 52, right: 16 }} />
    </TooltipProvider>
  </StrictMode>,
)
