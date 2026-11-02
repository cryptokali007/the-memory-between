from __future__ import annotations
import gc, hashlib, json, math, time
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent
SR = 48000
DURATION = 300
TOTAL = SR * DURATION
BPM = 72.0
BEATS = [
    (38, [0,3,7,10,14], "D minor add nine"),
    (34, [0,4,7,11,14], "B flat major seven"),
    (41, [0,4,7,9,14], "F major add six"),
    (36, [0,4,7,14,19], "C add nine"),
    (31, [0,3,7,10,14], "G minor nine"),
    (38, [0,5,7,10,14], "D minor over A"),
    (34, [0,4,7,11,14], "B flat major nine"),
    (41, [0,4,7,9,14], "F major add six"),
    (36, [0,4,7,14,19], "C major nine"),
    (31, [0,3,7,10,14], "G minor nine"),
    (38, [0,3,7,10,14], "D minor seven"),
    (34, [0,4,7,11,14], "B flat major seven"),
    (36, [0,4,7,14,19], "C add nine"),
    (33, [0,4,7,10,14], "A minor nine"),
    (41, [0,4,7,9,14], "F major nine"),
    (31, [0,3,7,10,14], "G minor nine"),
    (34, [0,4,7,11,14], "B flat major seven"),
    (36, [0,4,7,14,19], "C major nine"),
    (38, [0,3,7,10,14], "D minor nine"),
    (38, [0,3,7,10,14], "D minor add nine resolve"),
]
LEVEL = [0.30,0.34,0.40,0.49,0.58,0.66,0.72,0.68,0.64,0.66,
         0.60,0.53,0.62,0.70,0.78,0.58,0.64,0.78,0.90,0.38]
MELODY = [74,69,72,74,77,76,74,72,69,72,74,77,81,79,77,74]
PARTIALS = {
    "pad": [(1,.57),(.5,.20),(1.5,.15),(2,.07)],
    "strings": [(1,.64),(2,.23),(3,.09),(4,.04)],
    "bell": [(1,.70),(2.01,.22),(3.93,.07),(6.1,.025)],
    "sine": [(1,1.0)]
}

def sha(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1<<20),b""): h.update(block)
    return h.hexdigest()

def midi(note): return 440.0 * 2.0 ** ((note-69)/12.0)
def pan_gains(pan):
    angle=(max(-1.0,min(1.0,pan))+1.0)*math.pi/4.0
    return math.cos(angle), math.sin(angle)

def add_note(track, start, duration, hz, amp, timbre, pan, attack=.08, release=.2,
             phase=0.0, wobble=0.0):
    i0=max(0,int(round(start*SR))); n=max(1,int(round(duration*SR)))
    i1=min(TOTAL,i0+n); n=i1-i0
    if n<=0 or amp<=0: return
    t=np.arange(n,dtype=np.float32)/SR; dur=n/SR
    a=max(.001,min(attack,dur)); r=max(.001,min(release,dur))
    env=np.sin(np.clip(t/a,0,1)*(math.pi/2))**2
    env*=np.sin(np.clip((dur-t)/r,0,1)*(math.pi/2))**2
    if timbre=="bell": env*=np.exp(-t/max(.04,duration*.46))
    elif timbre=="strings": env*=(.94+.06*np.sin(2*math.pi*.31*t+wobble))
    elif timbre=="pad": env*=(.96+.04*np.sin(2*math.pi*.12*t+wobble))
    wave=np.zeros(n,dtype=np.float32)
    for harmonic,level in PARTIALS[timbre]:
        f=hz*harmonic
        if f < SR*.48:
            wave+=np.sin((2*math.pi*f*t+phase+.17*harmonic).astype(np.float32))*level
    if timbre in ("pad","strings"):
        for cents,level in ((-2.8,.14),(3.2,.11)):
            f=hz*2**(cents/1200)
            if f < SR*.48:
                wave+=np.sin((2*math.pi*f*t+phase+.4).astype(np.float32))*level
    wave*=env.astype(np.float32)*np.float32(amp)
    left,right=pan_gains(pan)
    track[0,i0:i1]+=wave*np.float32(left)
    track[1,i0:i1]+=wave*np.float32(right)

def synth_score():
    track=np.zeros((2,TOTAL),dtype=np.float32)
    beat_s=60.0/BPM
    arrangement=[]
    for i,(root,chord,name) in enumerate(BEATS):
        start=i*15.0; intensity=LEVEL[i]; duration=15.7 if i<19 else 15
        amps=[1,.82,.72,.63,.48]; order=[0,2,1,3,4]
        pans=[-.52,.30,-.18,.53,-.38]
        for pos,j in enumerate(order):
            add_note(track,start,duration,midi(root+12+chord[j]),.021*intensity*amps[j],
                     "pad",pans[pos],3.0,3.0,wobble=i*.33+pos)
        add_note(track,start,duration,midi(root),.038*intensity,"strings",-.08,1.7,2.6,wobble=i*.7)
        add_note(track,start+.5,14,midi(root+12),.018*intensity,"strings",.08,1.4,2.2,.4,i*.2)
        if i in (3,5,6,8,12,13,14,17,18):
            add_note(track,start+.2,11.6,midi(root+24),.011*intensity,"pad",0,2.8,2.8,.2,.5)
        if 3<=i<19:
            arp=.022*intensity*(.62 if i in (10,11,15,16) else 1)
            pattern=[0,2,1,4,3,2,0,1]; t=start+.10; k=0
            while t < (i+1)*15-.12:
                note=root+24+chord[pattern[k%len(pattern)]]
                pan=(-.62 if k%2==0 else .62)*(.55+.35*((k%4)/3))
                add_note(track,t,.66,midi(note),arp,"bell",pan,.025,.52,(k%5)*.21)
                t+=beat_s/2; k+=1
        if 2<=i<19:
            for j,offset in enumerate((1.0,4.2,7.4,10.6,13.1)):
                note=MELODY[(i*3+j)%len(MELODY)]-(12 if j in (1,4) else 0)
                pan=(-.36,.22,.40,-.12,.12)[j]
                add_note(track,start+offset,3.35,midi(note),.031*intensity,"strings",pan,.60,1.15,j*.37,i*.13)
        if i>=3 and i not in (10,11,15,16,19):
            for offset in (.12,5.12,10.12):
                add_note(track,start+offset,.95,midi(root),.021*intensity,"sine",0,.08,.72,.1)
        arrangement.append({"start_seconds":i*15,"end_seconds":(i+1)*15,
                            "chord":name,"intensity":intensity})
    fadein=np.clip(np.arange(TOTAL,dtype=np.float32)/(SR*4),0,1)
    fadein=np.sin(fadein*math.pi/2)**2
    fadeout=np.clip((TOTAL-np.arange(TOTAL,dtype=np.float32))/(SR*7),0,1)
    fadeout=np.sin(fadeout*math.pi/2)**2
    track*= (fadein*fadeout)[None,:]
    track*=np.float32(5.0)
    peak=float(np.max(np.abs(track)))
    if peak>.78: track*=np.float32(.78/peak)
    return track,arrangement

def add_hit(track,start,amp,pan):
    dur=.62; i0=int(round(start*SR)); n=min(int(dur*SR),TOTAL-i0)
    if n<=0: return
    t=np.arange(n,dtype=np.float32)/SR
    sweep=96*np.exp(-t*2.5)+42
    phase=2*math.pi*np.cumsum(sweep,dtype=np.float32)/SR
    low=np.sin(phase)*np.exp(-t*6)
    m=min(int(.012*SR),n)
    rng=np.random.default_rng(int(start*1000)+409)
    click=np.zeros(n,dtype=np.float32)
    click[:m]=rng.standard_normal(m).astype(np.float32)*np.linspace(.12,0,m,dtype=np.float32)
    wave=(low*.75+click*.25)*amp
    left,right=pan_gains(pan)
    track[0,i0:i0+n]+=wave*np.float32(left); track[1,i0:i0+n]+=wave*np.float32(right)

def synth_sfx():
    track=np.zeros((2,TOTAL),dtype=np.float32); events=[]
    swells={0,3,4,5,6,8,9,11,12,13,14,15,16,17,18,19}
    hits={0,3,5,8,9,12,13,15,17,18,19}
    for i,(root,chord,_) in enumerate(BEATS):
        pan=-.48 if i%2==0 else .48; start=i*15.0
        if i in swells:
            at=max(.12,start-1.65); dur=1.82; n=int(dur*SR)
            rng=np.random.default_rng(7200+i)
            noise=rng.standard_normal(n).astype(np.float32)
            hp=np.empty_like(noise); hp[0]=noise[0]; hp[1:]=noise[1:]-.965*noise[:-1]
            t=np.arange(n,dtype=np.float32)/SR
            env=np.sin(np.clip(t/dur,0,1)*(math.pi/2))**2
            env*=np.clip((dur-t)/.32,0,1)
            sweep=np.sin(2*math.pi*(150*t+80*t*t))
            wave=(hp*.23+sweep*.77)*env*.022
            i0=int(at*SR); i1=min(TOTAL,i0+n); l,r=pan_gains(pan)
            track[0,i0:i1]+=wave[:i1-i0]*np.float32(l)
            track[1,i0:i1]+=wave[:i1-i0]*np.float32(r)
            events.append({"start_seconds":round(at,3),"duration_seconds":dur,
                           "type":"soft filtered data swell","pan":pan})
        if i in hits:
            at=start+.08; atpan=-pan
            add_hit(track,at,.05 if i in (0,18,19) else .032,atpan)
            events.append({"start_seconds":round(at,3),"duration_seconds":.62,
                           "type":"restrained low pulse and transient","pan":atpan})
        if i in (4,6,12,14,17):
            at=start+7.2; freq=midi(root+36+chord[2])
            add_note(track,at,1.5,freq,.010,"bell",-pan*.55,.02,1.35,.2)
            events.append({"start_seconds":round(at,3),"duration_seconds":1.5,
                           "type":"soft data chime","pan":-pan*.55})
    fade=np.clip((TOTAL-np.arange(TOTAL,dtype=np.float32))/(SR*4),0,1)
    track*=fade[None,:]
    track*=np.float32(8.0)
    peak=float(np.max(np.abs(track)))
    if peak>.32: track*=np.float32(.32/peak)
    return track,events

def stats(track):
    peak=float(np.max(np.abs(track)))
    rms=float(np.sqrt(np.mean(track.astype(np.float64)**2)))
    return {"peak_dbfs":round(20*math.log10(max(peak,1e-12)),3),
            "rms_dbfs":round(20*math.log10(max(rms,1e-12)),3),
            "frames":int(track.shape[1]),"sample_rate":SR,"channels":2,"format":"PCM_24"}

def music_bands(track):
    mono=(track[0]+track[1])*.5
    window=np.hanning(SR).astype(np.float32); coherent=float(window.sum())
    values=[]
    for sec in range(DURATION):
        spectrum=np.abs(np.fft.rfft(mono[sec*SR:(sec+1)*SR]*window))*(2.0/coherent)
        bins=spectrum[:10000].reshape(500,20)
        rms=np.sqrt(np.mean(bins*bins,axis=1))
        values.append([round(20*math.log10(max(float(x),1e-10)),2) for x in rms])
    return {"source":"score.wav","measurement":"mean-bin spectral RMS per one-second frame, mono fold-down",
            "unit":"dBFS approximate amplitude","sample_rate":SR,"frame_seconds":1,
            "band_width_hz":20,"frequency_range_hz":[0,10000],
            "band_centers_hz":[10+20*i for i in range(500)],"frames":values}

def motion_envelopes(track):
    """50 Hz centered STFT bands; output uses robust per-band p5/p95 scaling."""
    mono=((track[0]+track[1])*.5).astype(np.float32,copy=False)
    hop=960; win_n=4096; frames_n=DURATION*50
    offsets=np.arange(win_n,dtype=np.int64)-win_n//2
    window=np.hanning(win_n).astype(np.float32)
    denom=float(np.sum(window*window))
    hz=np.fft.rfftfreq(win_n,d=1.0/SR)
    one_sided=np.full(len(hz),2.0,dtype=np.float32)
    one_sided[0]=1.0
    one_sided[-1]=1.0
    masks={
        "low":(hz>=28)&(hz<180),
        "mid":(hz>=180)&(hz<2200),
        "high":(hz>=2200)&(hz<=12000),
        "rms":(hz>=20)&(hz<=12000),
    }
    raw={name:np.zeros(frames_n,dtype=np.float32) for name in masks}
    batch_size=256
    for first in range(0,frames_n,batch_size):
        last=min(frames_n,first+batch_size)
        centers=np.arange(first,last,dtype=np.int64)*hop
        indices=centers[:,None]+offsets[None,:]
        valid=(indices>=0)&(indices<len(mono))
        frames=mono[np.clip(indices,0,len(mono)-1)]
        frames=frames*valid.astype(np.float32)
        spec=np.fft.rfft(frames*window[None,:],axis=1)
        power=(spec.real*spec.real+spec.imag*spec.imag).astype(np.float32)
        for name,mask in masks.items():
            energy=np.sum(power[:,mask]*one_sided[mask][None,:],axis=1,dtype=np.float64)/denom
            raw[name][first:last]=np.sqrt(np.maximum(energy,0)).astype(np.float32)
    envelopes={}; normalization={}
    for name,values in raw.items():
        db=20*np.log10(np.maximum(values.astype(np.float64),1e-12))
        p5=float(np.percentile(db,5)); p95=float(np.percentile(db,95))
        denom_db=max(1e-6,p95-p5)
        envelopes[name]=np.round(np.clip((db-p5)/denom_db,0,1),4).tolist()
        normalization[name]={"method":"robust percentile linear remap in dBFS",
            "percentiles":[5,95],"raw_p5_dbfs":round(p5,3),"raw_p95_dbfs":round(p95,3),
            "raw_min_dbfs":round(float(np.min(db)),3),"raw_max_dbfs":round(float(np.max(db)),3)}
    return {"format_version":1,"source":"score.wav","sample_rate_hz":SR,
        "duration_seconds":DURATION,"frame_rate_hz":50,"frame_hop_samples":hop,
        "frame_time_step_seconds":hop/SR,"frame_count":frames_n,
        "stft":{"window":"Hann","window_samples":win_n,
            "window_duration_seconds":win_n/SR,"centering":"window centered on frame time, zero-padded at boundaries",
            "band_measurement":"square-root of weighted one-sided spectral energy divided by Hann window energy",
            "channels":"stereo averaged to mono before analysis"},
        "band_limits_hz":{"low":[28,180],"mid":[180,2200],"high":[2200,12000],"rms":[20,12000]},
        "normalization":normalization,"envelopes":envelopes}


def main():
    start=time.perf_counter()
    score,arrangement=synth_score()
    sf.write(ROOT/"score.wav",score.T,SR,subtype="PCM_24")
    score_stats=stats(score)
    bands=music_bands(score)
    (ROOT/"music_bands.json").write_text(json.dumps(bands,separators=(",",":"))+"\n",encoding="utf-8")
    del bands; gc.collect()
    motion=motion_envelopes(score)
    (ROOT/"motion_envelopes.json").write_text(json.dumps(motion,separators=(",",":"))+"\n",encoding="utf-8")
    del motion; gc.collect()
    sfx,events=synth_sfx()
    sf.write(ROOT/"sfx.wav",sfx.T,SR,subtype="PCM_24")
    sfx_stats=stats(sfx)
    manifest={"title":"THE MEMORY BETWEEN","duration_seconds":DURATION,"sample_rate":SR,
              "channels":2,"bit_depth":24,"score":score_stats,"sfx":sfx_stats,
              "score_style":"original synthetic orchestral score with evolving pads, bowed tones, bass pulse, arpeggio and recurring memory motif",
              "sfx_style":"original procedural filtered swells, low pulses and soft chimes; no sampled media",
              "arrangement":arrangement,"sfx_events":events,
              "score_sha256":sha(ROOT/"score.wav"),"sfx_sha256":sha(ROOT/"sfx.wav"),
              "music_bands_sha256":sha(ROOT/"music_bands.json"),
              "motion_envelopes_sha256":sha(ROOT/"motion_envelopes.json"),
              "script_sha256":sha(Path(__file__)),"elapsed_seconds":round(time.perf_counter()-start,3)}
    (ROOT/"score_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(f"COMPLETE score and SFX: {score_stats['frames']} frames at {SR} Hz stereo.")
    print(f"score peak/RMS {score_stats['peak_dbfs']}/{score_stats['rms_dbfs']} dBFS; SFX {sfx_stats['peak_dbfs']}/{sfx_stats['rms_dbfs']} dBFS.")
if __name__=="__main__": main()




