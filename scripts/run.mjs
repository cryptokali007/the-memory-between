import {spawnSync} from 'node:child_process';
import {readFileSync, existsSync} from 'node:fs';
import {dirname, resolve, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const dir = join(root, 'node_modules', 'hyperframes');
  const manifest = join(dir, 'package.json');
  if (!existsSync(manifest)) { console.error('Install the locked dependencies first: npm ci'); process.exit(1); }
  const pkg = JSON.parse(readFileSync(manifest, 'utf8'));
  const bin = typeof pkg.bin === 'string' ? pkg.bin : pkg.bin?.hyperframes;
  if (!bin) { console.error('The installed hyperframes package does not declare a CLI.'); process.exit(1); }
  const env = {...process.env, HYPERFRAMES_NO_TELEMETRY: '1', DO_NOT_TRACK: '1', HYPERFRAMES_NO_UPDATE_CHECK: '1', HYPERFRAMES_SKIP_SKILLS: '1', HYPERFRAMES_NO_CAPTURE_DOTENV: '1'};
  const result = spawnSync(process.execPath, [resolve(dir, bin), ...process.argv.slice(2)], {cwd: root, env, stdio: 'inherit', windowsHide: true});
  if (result.error) { console.error(result.error.message); process.exitCode = 1; } else process.exitCode = result.status ?? 1;
}
