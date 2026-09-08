import {resolve} from 'node:path';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// Vite 以 CJS 方式加载本配置，__dirname 可用。
const configDirectory = __dirname;

// 构建产物由既有 FastAPI 静态目录提供；固定文件名便于服务端模板引用，
// 业务员不需要额外启动 Node 开发服务器。
export default defineConfig({
  root: configDirectory,
  plugins: [react()],
  base: '/static/auto-approval/',
  build: {
    outDir: resolve(configDirectory, '../assistant/web/static/auto-approval'),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/index.js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
});
