"""Reviewed, targeted integration; preserve the already-rendered first292s."""
from pathlib import Path
import hashlib

root=Path(__file__).resolve().parent.parent
module=root/'assets/abstract-walrus.js'
if not module.is_file():raise RuntimeError('Review and stage abstract-walrus.js first')
path=root/'film.js';source=path.read_text(encoding='utf-8')
import_line="import {createAbstractWalrus} from './assets/abstract-walrus.js';"
if import_line in source:raise RuntimeError('Abstract closing integration already applied')
start=source.index('function b20(sc)');end=source.index('function normalizeBands',start)
old=source[start:end]
tail='right.rotation.z=-.025*Math.sin(t*.22+.5);});return s;}'
if old.count(tail)!=1:raise RuntimeError('Expected unique original closing return')
new_tail='''right.rotation.z=-.025*Math.sin(t*.22+.5);});
// The accepted core render ends at291+59/60. Keep that art byte-identical.
const legacy=new THREE.Group();for(const child of [...s.hero.children])legacy.add(child);s.hero.add(legacy);
const abstract=createAbstractWalrus(THREE);abstract.root.position.set(0,1,0);abstract.root.visible=false;s.hero.add(abstract.root);s.abstract=abstract;
s.animate.push((t,audio)=>{const closing=t>=7;legacy.visible=!closing;abstract.root.visible=closing;if(closing){const p=smooth((t-7)/1.15);abstract.root.scale.setScalar(.36+.64*p);abstract.seek(t-7,audio);}});
return s;}
'''
new=old.replace(tail,new_tail)
backup=root/'evidence/abstract-closing/film-before-abstract.js'
if backup.exists():raise RuntimeError('Existing backup: review before repeating integration')
backup.parent.mkdir(parents=True,exist_ok=True);backup.write_text(source,encoding='utf-8')
source=source[:start]+new+source[end:]
source=import_line+'\n'+source
path.write_text(source,encoding='utf-8')
print('Integrated abstract sculpture for292–300s only; baseline QA required.')
print('Module SHA256 '+hashlib.sha256(module.read_bytes()).hexdigest())
