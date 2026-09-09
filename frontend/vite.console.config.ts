import {resolve} from 'node:path';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// Vite 以 CJS 方式加载本配置，__dirname 可用。
const configDirectory = __dirname;

// 操作台（工作台/物流/跟进/任务/报表/诊断）的 Astryx React 构建；
// 与自动审批页各自独立产出，服务端模板按固定文件名引用。
export default defineConfig({
  root: configDirectory,
  plugins: [react()],
  base: '/static/console/',
  build: {
    outDir: resolve(configDirectory, '../assistant/web/static/console'),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: resolve(configDirectory, 'console.html'),
      output: {
        entryFileNames: 'assets/index.js',
        chunkFileNames: 'assets/[name].js',
        // 模板按固定名引用；本构建没有图片/字体等其它资源。
        assetFileNames: 'assets/index[extname]',
      },
    },
  },
});
