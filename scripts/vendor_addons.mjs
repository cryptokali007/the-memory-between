import {readFileSync,writeFileSync,copyFileSync,mkdirSync} from 'node:fs';
import {resolve,dirname,relative,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');const config=JSON.parse(readFileSync(join(root,'runtime.json'),'utf8'));
const source=join(config.toolchainRoot,'node_modules','three','examples','jsm'),target=join(root,'assets','vendor','addons');
const pending=['environments/RoomEnvironment.js','postprocessing/EffectComposer.js','postprocessing/RenderPass.js','postprocessing/UnrealBloomPass.js','postprocessing/OutputPass.js'];const done=new Set(),manifest=[];
while(pending.length){const name=pending.pop();if(done.has(name))continue;done.add(name);const file=resolve(source,name),rel=relative(source,file);if(rel.startsWith('..'))throw new Error('Addon import outside upstream addons');const text=readFileSync(file,'utf8');for(const m of text.matchAll(/(?:from\s*|import\s*)['"](\.[^'"]+\.js)['"]/g)){const dep=relative(source,resolve(dirname(file),m[1])).replaceAll('\\','/');pending.push(dep)}const dest=join(target,rel);mkdirSync(dirname(dest),{recursive:true});copyFileSync(file,dest);manifest.push({path:rel,sha256:createHash('sha256').update(text).digest('hex')});}
writeFileSync(join(root,'evidence','ADDONS_MANIFEST.json'),JSON.stringify({three:'0.186.1',files:manifest},null,2));console.log('Vendored',manifest.length,'actual addon dependencies');
