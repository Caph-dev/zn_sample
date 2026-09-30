#!/usr/bin/env node
/** Cross-platform build entry. Build tools use this Node, never shell shims. */
import {spawnSync} from 'node:child_process';
import {copyFileSync, mkdirSync, readFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const environment = {...process.env};
for (const variable of Object.keys(environment)) {
  if (
    /^(PYTHON|NODE_|DYLD_|LD_|FEISHU_|LLM_|ZN_SAMPLE_|CONTENT_REVIEW_|ARK_|TIKHUB_|VITE_|NPM_CONFIG_|npm_config_)/.test(variable)
    || /(?:API_KEY|APP_SECRET|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD|CREDENTIAL)/i.test(variable)
    || ['VIRTUAL_ENV', 'CONDA_PREFIX', '__PYVENV_LAUNCHER__'].includes(variable)
  ) {
    delete environment[variable];
  }
}

function runNodeTool(relativeScript, argumentsList) {
  const result = spawnSync(process.execPath, [resolve(projectRoot, relativeScript), ...argumentsList], {
    cwd: projectRoot,
    env: environment,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`Static build tool failed: ${relativeScript}`);
  }
}

function buildTheme(checkOnly = false) {
  mkdirSync(resolve(projectRoot, 'build/theme'), {recursive: true});
  runNodeTool('node_modules/@astryxdesign/cli/clients/cli/bin/astryx.mjs', [
    'theme', 'build', 'src/themes/geist/geistTheme.ts', '-o', 'build/theme/geist-theme.css',
    ...(checkOnly ? ['-c'] : []),
  ]);
  if (!checkOnly) {
    mkdirSync(resolve(projectRoot, 'assistant/web/static'), {recursive: true});
    copyFileSync(resolve(projectRoot, 'build/theme/geist-theme.css'), resolve(projectRoot, 'assistant/web/static/geist-theme.css'));
  }
}

function buildFonts() {
  const fontsDirectory = resolve(projectRoot, 'assistant/web/static/fonts');
  mkdirSync(fontsDirectory, {recursive: true});
  for (const [sourceName, destinationName] of [
    ['geist-sans/Geist-Variable.woff2', 'Geist.woff2'],
    ['geist-mono/GeistMono-Variable.woff2', 'GeistMono.woff2'],
  ]) {
    copyFileSync(resolve(projectRoot, 'node_modules/geist/dist/fonts', sourceName), resolve(fontsDirectory, destinationName));
  }
}

function validateThemeOrder() {
  for (const [templateName, buildName] of [['console', 'console'], ['auto_approval', 'auto-approval']]) {
    const template = readFileSync(resolve(projectRoot, `assistant/web/templates/${templateName}.html`), 'utf8');
    const componentPosition = template.indexOf(`/static/${buildName}/assets/index.css`);
    const themePosition = template.indexOf('/static/geist-theme.css');
    if (componentPosition < 0 || themePosition <= componentPosition) {
      throw new Error(`Theme must follow component CSS: ${templateName}`);
    }
  }
}

const mode = process.argv[2];
if (process.argv.length !== 3 || !['theme', 'check-theme', 'fonts', 'web'].includes(mode)) {
  console.error('Usage: build_static_assets.mjs theme|check-theme|fonts|web');
  process.exitCode = 2;
} else {
  try {
    if (mode === 'theme' || mode === 'check-theme') buildTheme(mode === 'check-theme');
    if (mode === 'fonts') buildFonts();
    if (mode === 'web') {
      buildTheme();
      buildFonts();
      runNodeTool('node_modules/typescript/bin/tsc', ['-p', 'frontend/tsconfig.json', '--noEmit']);
      for (const configName of ['vite.config.ts', 'vite.console.config.ts']) {
        runNodeTool('node_modules/vite/bin/vite.js', ['build', '--config', `frontend/${configName}`]);
      }
      validateThemeOrder();
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 2;
  }
}
