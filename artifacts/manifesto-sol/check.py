"""Inspect the encoded film, not the live composition.
Usage: python3 check.py /path/to/brnrd-continuity-sol.mp4 /path/to/review
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
from PIL import Image, ImageDraw

film=Path(sys.argv[1]).resolve()
root=Path(sys.argv[2]).resolve()
root.mkdir(parents=True,exist_ok=True)
def run(cmd):
    return subprocess.run(cmd,capture_output=True,text=True,check=True)
probe=json.loads(run(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(film)]).stdout)
run(['ffmpeg','-v','error','-i',str(film),'-f','null','-'])
levels_text=run(['ffmpeg','-hide_banner','-i',str(film),'-vn','-af','loudnorm=I=-16:TP=-1.2:LRA=10:print_format=json','-f','null','-']).stderr
levels=json.loads(levels_text[levels_text.rfind('{'):levels_text.rfind('}')+1])
black=run(['ffmpeg','-hide_banner','-i',str(film),'-an','-vf','crop=1920:804:0:138,blackdetect=d=0.25:pix_th=0.05','-f','null','-']).stderr
black_lines=[s for s in black.splitlines() if 'black_start:' in s]
def contact(times,name,cols=3):
    w,h=640,360
    directory=root/name
    directory.mkdir(exist_ok=True)
    sheet=Image.new('RGB',(w*cols,(h+32)*((len(times)+cols-1)//cols)),(10,14,19))
    draw=ImageDraw.Draw(sheet)
    samples=[]
    for i,t in enumerate(times):
        file=directory/f'{t:06.2f}.jpg'
        run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(film),'-frames:v','1','-q:v','2',str(file)])
        im=Image.open(file).convert('RGB').resize((w,h))
        samples.append(np.asarray(im,dtype=np.float32))
        x=(i%cols)*w;y=(i//cols)*(h+32)
        sheet.paste(im,(x,y));draw.text((x+16,y+h+9),f'{t:.2f} s / encoded MP4',fill=(220,225,230))
    sheet.save(root/f'{name}.jpg',quality=94)
    return [float(np.mean(np.abs(b-a))) for a,b in zip(samples,samples[1:])]
contact([2,7,10.5,14.7,20.5,27,32.5,35,38,40.5,45,50,56,62,66,71.5,76,78.5,81],'film-contact')
motion={
 'fault-motion':contact([37.9+i*.1 for i in range(12)],'fault-motion',4),
 'basis-motion':contact([48.3+i*.1 for i in range(12)],'basis-motion',4),
 'type-motion':contact([73.5+i/30 for i in range(12)],'type-motion',4),
}
for start,duration,name in [(34,9,'fault-playback'),(48.2,2.4,'basis-playback'),(73.4,6.9,'type-playback')]:
    run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(film),'-t',str(duration),'-c:v','libx264','-crf','18','-preset','fast','-c:a','aac','-b:a','192k','-movflags','+faststart',str(root/f'{name}.mp4')])
video=next(s for s in probe['streams'] if s['codec_type']=='video')
audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
assert video['nb_read_frames']=='2520',video['nb_read_frames']
assert (video['width'],video['height'])==(1920,1080)
assert video['avg_frame_rate']=='30/1'
assert abs(float(probe['format']['duration'])-84)<.1
assert float(levels['input_tp'])<0,levels
assert all(max(v)>1 for v in motion.values()),motion
report={'film':str(film),'sha256':hashlib.sha256(film.read_bytes()).hexdigest(),'decode':'entire video and audio decoded without errors','video':video,'audio':audio,'format':probe['format'],'audio_loudness':levels,'black_intervals':black_lines,'sampled_motion_mean_pixel_difference':motion}
(root/'integrity.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'duration':probe['format']['duration'],'frames':video['nb_read_frames'],'size':probe['format']['size'],'loudness':levels,'black_intervals':black_lines,'motion':motion},indent=2))
