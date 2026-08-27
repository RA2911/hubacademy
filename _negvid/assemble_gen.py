import subprocess, os, json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
scenes = json.load(open('scenes.json'))
ids = [s['id'] for s in scenes]
T = 1.0
trans = ['fadewhite', 'dissolve', 'wiperight', 'circleopen']
def run(c): subprocess.run(c, check=True)
def probe(f): return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', f]).strip())
durs = []
for s in ids:
    run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', '30', '-i', f'frames/{s}/%04d.png',
         '-c:v', 'libx264', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-r', '30', f'clip_{s}.mp4'])
    durs.append(probe(f'clip_{s}.mp4'))
offsets, length = [], durs[0]
for k in range(1, len(ids)):
    offsets.append(round(length - T, 3)); length = length + durs[k] - T
total = round(length, 3); fout = round(total - 0.7, 3)
parts, cur = [], '[0:v]'
for k in range(1, len(ids)):
    out = f'[v{k}]'
    parts.append(f'{cur}[{k}:v]xfade=transition={trans[(k-1) % len(trans)]}:duration={T}:offset={offsets[k-1]}{out}')
    cur = out
parts.append(f'{cur}fade=t=in:st=0:d=0.5:color=white,fade=t=out:st={fout}:d=0.7:color=black[vout]')
parts.append(f'[{len(ids)}:a]adelay=300:all=1,afade=t=out:st={fout}:d=0.7[aout]')
cmd = ['ffmpeg', '-y', '-loglevel', 'error']
for s in ids: cmd += ['-i', f'clip_{s}.mp4']
cmd += ['-i', 'cine.mp3', '-filter_complex', ';'.join(parts), '-map', '[vout]', '-map', '[aout]', '-t', str(total),
        '-c:v', 'libx264', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', 'cine.mp4']
run(cmd)
print('cine.mp4 built, duration', total)
