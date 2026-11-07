import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// Node's process global without adding @types/node to the type-checked config.
declare const process:{env:Record<string,string|undefined>};
// Loopback API port (default 8000); start_local.ps1 -ApiPort sets it when Windows
// reserves 8000 (e.g. a Hyper-V/WinNAT excluded port range).
const apiPort=/^\d{2,5}$/.test(process.env.TRANSPORT_ATLAS_API_PORT??'')?process.env.TRANSPORT_ATLAS_API_PORT:'8000';
const proxy={'/api':`http://127.0.0.1:${apiPort}`};
export default defineConfig({
  plugins:[react()],
  server:{host:'127.0.0.1',port:5173,proxy},
  preview:{host:'127.0.0.1',port:5173,proxy},
  build:{
    // MapLibre (~1 MB minified) changes far less often than the app; a separate
    // chunk keeps it cacheable. Its size is expected, so the warning threshold
    // reflects that one vendor chunk rather than hiding app growth.
    chunkSizeWarningLimit:1100,
    rollupOptions:{output:{manualChunks:(id:string)=>id.includes('node_modules/maplibre-gl')?'maplibre':id.includes('node_modules/react')?'react':undefined}}
  }
});
