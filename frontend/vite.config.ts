import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const proxy={'/api':'http://127.0.0.1:8000'};
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
