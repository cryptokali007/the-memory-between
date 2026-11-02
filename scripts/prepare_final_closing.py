"""Prepare a picture-only 292-300s snapshot without uploading irrelevant stems."""
from pathlib import Path
import ast
import hashlib
import json

project = Path(__file__).resolve().parents[1]
integration = project.parents[1] / 'Tools' / 'KaggleHyperframes'
stage = integration / 'staging' / 'memory-between-final-closing'
path = stage / 'index.html'
html = path.read_text(encoding='utf-8')
needle = 'id="operatorWalrus" class="clip" data-start="292"'
assert html.count(needle) == 1
# The remote runner offsets the film clock by292s. The player media clock is local.
html = html.replace(needle, 'id="operatorWalrus" class="clip" data-start="0"')
html = html.replace('src="assets/audio/score.wav"', 'src="assets/audio/score.wav"')
# Picture-only input: no audio needed on Kaggle; mix is already complete locally.
import re
html = re.sub(r'<audio\b[^>]*>.*?</audio>', '', html, flags=re.S|re.I)
path.write_text(html, encoding='utf-8')
runner = (integration / 'render_job.py').read_text(encoding='utf-8')
marker = 'for(const t of config.times){'
helper = '''const drawMedia=async t=>page.evaluate(async t=>{
 window.__drawFilm(t);const v=document.getElementById('operatorWalrus');
 if(v){if(v.readyState<2)await new Promise((ok,bad)=>{v.addEventListener('loadeddata',ok,{once:true});v.addEventListener('error',bad,{once:true});});
 const target=1+t-Number(document.documentElement.dataset.timeOffset||0);
 if(Math.abs(v.currentTime-target)>.001)await new Promise(ok=>{v.addEventListener('seeked',ok,{once:true});v.currentTime=target;});
 await new Promise(ok=>requestAnimationFrame(()=>requestAnimationFrame(ok)));}
 return window.__filmState;
},t);
'''
assert runner.count(marker) == 1
runner = runner.replace(marker, helper + marker)
runner = runner.replace('await page.evaluate(t=>{window.__drawFilm(t);return window.__filmState},t)', 'await drawMedia(t)')
runner = runner.replace('await page.evaluate(t=>{window.__drawFilm(t);return true},t)', 'await drawMedia(t)')
ast.parse(runner)
destination = integration / 'final_closing_media_runner.py'
destination.write_text(runner, encoding='utf-8')
lock = project / 'evidence' / 'CLOSING_WORDING_LOCK.json'
data = json.loads(lock.read_text(encoding='utf-8'))
data.update(character='Operator-supplied abstract mechanical Walrus Omni1.1 moving clip, on right; approved left text/layout preserved exactly.',
            artwork_approval='Approved this placement by direct operator reply 2026-10-25',
            voice_selection='Operator selected Kokoro af_heart; all20 unchanged beats generated offline on local NVIDIA CUDA and mix accepted by technical QA.')
lock.write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
receipt={'purpose':'Final approved ending picture only; canonical creative source preserved',
 'canonical_root':str(project),'staging_root':str(stage),
 'changes':['Video player start292->0 because runner film clock offset292', 'Audio omitted: final mix muxed locally after picture acceptance'],
 'source_video_start_seconds':1,'duration_seconds':8,'final_output':'3840x2160/60fps',
 'footage_native':'1920x1080/24fps; upscale and frame resampling',
 'runner_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
(project/'evidence'/'operator-omni-closing'/'STAGING_RECEIPT.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
print(destination)
