import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('dist/server', { recursive: true });
await build({ entryPoints: ['src/worker.ts'], outfile: 'dist/server/index.js', bundle: true, format: 'esm', platform: 'browser', target: 'es2022', minify: true, external: ['url', 'node:*'] });
try { await mkdir('dist/.openai', {recursive:true}); await copyFile('.openai/hosting.json','dist/.openai/hosting.json'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
