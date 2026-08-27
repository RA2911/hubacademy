"""Offline cinematic-intro generator for the AI Course Factory.
Usage:  python make_intro.py "Topic Name" [Level]
Produces static/videos/factory/<slug>/intro.mp4 (+ intro.jpg) and prints the
manifest line to paste into FACTORY_INTRO_VIDEOS. Runs the puppeteer+ffmpeg
pipeline locally (not on the web server)."""
import os, sys, io, re, json, shutil, subprocess

os.environ.setdefault('DATABASE_URL', 'sqlite:///_gen.db')
# load .env for OPENAI key
for line in io.open('.env', 'r', encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from openai import OpenAI
import fastapi_app as fa

ROOT = os.path.dirname(os.path.abspath(__file__))
TOPIC = sys.argv[1] if len(sys.argv) > 1 else 'Negotiations for Leaders'
LEVEL = sys.argv[2] if len(sys.argv) > 2 else 'Advanced'
slug = re.sub(r'[^a-z0-9]+', '-', TOPIC.lower()).strip('-')[:80]
WORK = os.path.join(ROOT, '_gen')
client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])


def match_images(topic):
    t = topic.lower()
    for kws, imgs in fa.COURSE_IMAGE_RULES:
        if any(k in t for k in kws):
            return imgs
    return fa.DEFAULT_COURSE_IMAGES


def run(c, **kw): subprocess.run(c, check=True, **kw)


# ---- fresh work dir ----
if os.path.isdir(WORK):
    shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(os.path.join(WORK, 'frames'), exist_ok=True)
for f in ('scene_cine.html', 'logo.png', 'capture_gen.js', 'assemble_gen.py'):
    shutil.copy(os.path.join(ROOT, '_negvid', f), os.path.join(WORK, f))

# ---- 1) images (3 distinct, high-res) ----
imgs = match_images(TOPIC)
pick = [imgs[i % len(imgs)] for i in range(3)]
for i, u in enumerate(pick, 1):
    u2 = u.replace('w=900', 'w=1920')
    run(['curl', '-s', '-L', '-o', os.path.join(WORK, f'v{i}bg.jpg'),
         '-H', 'User-Agent: Mozilla/5.0', u2])
print('images:', pick)

# ---- 2) AI scene text + narration ----
schema = ('{"narration":"70-80 word spoken intro, ~25s, ends with: Start your free preview now.",'
          '"scenes":[{"eyebrow":"3-4 WORD UPPERCASE tag","title":"Two short words\\nsecond line","sub":"one short line",'
          '"points":["Word","Word","Word"]}]}  (exactly 3 scenes)')
msg = (f'You are scripting a 28-second cinematic course-intro video for a {LEVEL} course on "{TOPIC}".\n'
       f'Return ONLY valid JSON: {schema}\n'
       'Titles: punchy, 2 short lines (<=14 chars/line). points: 3 single keywords each scene. '
       'The 3 scenes should progress through the core ideas of the topic. Keep it inspiring and specific to the topic.')
resp = client.chat.completions.create(model='gpt-4o', temperature=0.6, max_tokens=600,
                                      messages=[{'role': 'user', 'content': msg}],
                                      response_format={'type': 'json_object'})
data = json.loads(resp.choices[0].message.content)
scenes_in = (data.get('scenes') or [])[:3]
while len(scenes_in) < 3:
    scenes_in.append({'eyebrow': TOPIC[:24].upper(), 'title': TOPIC, 'sub': '', 'points': []})

# narration audio (Emma / nova)
speech = client.audio.speech.create(model='tts-1', voice='nova', input=data['narration'])
audio = getattr(speech, 'content', None) or speech.read()
io.open(os.path.join(WORK, 'cine.mp3'), 'wb').write(audio)

durs = [9.5, 9.0, 9.5]
scenes = []
for i, sc in enumerate(scenes_in):
    scenes.append({'id': f's{i+1}', 'dur': durs[i], 'bg': f'v{i+1}bg.jpg', 'kb': i + 1,
                   'eyebrow': sc.get('eyebrow', ''), 'title': sc.get('title', ''),
                   'sub': sc.get('sub', ''), 'points': '|'.join((sc.get('points') or [])[:3])})
json.dump(scenes, io.open(os.path.join(WORK, 'scenes.json'), 'w', encoding='utf-8'))
print('scenes:', [s['title'].replace(chr(10), ' ') for s in scenes])

# ---- 3) render + assemble ----
env = {**os.environ, 'NODE_PATH': os.path.join(ROOT, '_s3vid', 'node_modules')}
run(['node', 'capture_gen.js'], cwd=WORK, env=env)
run([sys.executable, 'assemble_gen.py'], cwd=WORK)

# ---- 4) publish ----
dest = os.path.join(ROOT, 'static', 'videos', 'factory', slug)
os.makedirs(dest, exist_ok=True)
shutil.copy(os.path.join(WORK, 'cine.mp4'), os.path.join(dest, 'intro.mp4'))
run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', '3', '-i', os.path.join(dest, 'intro.mp4'),
     '-frames:v', '1', '-q:v', '3', os.path.join(dest, 'intro.jpg')])

print('\nDONE ->', os.path.join('static', 'videos', 'factory', slug, 'intro.mp4'))
print('\nAdd to FACTORY_INTRO_VIDEOS:')
print(f"    '{fa.normalize_topic(TOPIC)}': {{'src': '/static/videos/factory/{slug}/intro.mp4',")
print(f"        'poster': '/static/videos/factory/{slug}/intro.jpg', 'dur': '0:28'}},")
