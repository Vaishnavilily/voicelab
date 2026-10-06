import os, json, time, uuid, base64, subprocess, threading, pathlib, tempfile
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from bson.errors import InvalidId
import gridfs
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent.parent / ".env")
db = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("MONGODB_DB", "voicelab")]
fs = gridfs.GridFS(db)
KUSER = os.environ["KAGGLE_USERNAME"]
SLUG = f"{KUSER}/voicelab-tts"
WORK = pathlib.Path(tempfile.gettempdir()) / "voicelab_jobs"; WORK.mkdir(exist_ok=True)
TEMPLATE = (pathlib.Path(__file__).parent / "kernel_template.py").read_text()
LOCK = threading.Lock()  # one clip at a time
MAX_REF_SECONDS = 12
app = Flask(__name__); CORS(app)

def to_wav(raw):  # any browser format -> 24 kHz mono wav, leading silence trimmed, 0.4 s silence added at the end
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "24000",
                        "-af", "silenceremove=start_periods=1:start_threshold=-45dB,apad=pad_dur=0.4",
                        "-f", "wav", "pipe:1"], input=raw, capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr.decode())
    secs = (len(p.stdout) - 44) / 48000
    # Never cut the sample: the transcript has to match the audio exactly, and a cut mid-word breaks that.
    if secs > MAX_REF_SECONDS + 0.5:
        raise ValueError(f"The sample is {secs:.1f} s long. Trim it to {MAX_REF_SECONDS} s or less, ending in a pause "
                         "between words, and make the transcript match what is left.")
    return p.stdout

def to_mp3(wav, mp3):
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "2", str(mp3)],
                       capture_output=True, text=True)
    if p.returncode: raise RuntimeError("Could not make the MP3: " + p.stderr)

@app.get("/api/voices")
def voices():
    return jsonify([{"id": str(v["_id"]), "name": v["name"], "description": v.get("description", ""),
                     "transcript": v["transcript"]} for v in db.voices.find().sort("_id", -1)])

@app.post("/api/voices")
def add_voice():
    f, name, tr = request.files.get("audio"), request.form.get("name", "").strip(), request.form.get("transcript", "").strip()
    if not (f and name and tr): return jsonify(error="Name, transcript and an audio sample are required."), 400
    try: wav = to_wav(f.read())
    except ValueError as e: return jsonify(error=str(e)), 400
    except Exception as e: return jsonify(error=f"Could not read the audio: {e}"), 400
    fid = fs.put(wav, filename=name + ".wav")
    db.voices.insert_one({"name": name, "description": request.form.get("description", ""),
                          "transcript": tr, "file_id": fid})
    return jsonify(ok=True)

@app.delete("/api/voices/<vid>")
def del_voice(vid):
    v = db.voices.find_one_and_delete({"_id": ObjectId(vid)})
    if v: fs.delete(v["file_id"])
    return jsonify(ok=True)

def kaggle(*args):
    return subprocess.run(["kaggle", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})

def set_job(jid, **kw):  # job state lives in MongoDB, so it survives a server restart
    db.jobs.update_one({"_id": jid}, {"$set": {**kw, "updated": time.time()}})

def wait_for_kaggle(jid, slug, started):
    fails = 0
    while time.time() - started < 25 * 60:
        r = kaggle("kernels", "status", slug)
        if r.returncode:  # the CLI itself failed (network, etc.). Don't treat that as a failed run.
            fails += 1
            if fails >= 12: raise RuntimeError("Could not reach Kaggle to check the run: " + (r.stdout + r.stderr).strip())
        else:
            fails = 0; s = r.stdout.upper()
            if "COMPLETE" in s: return
            if "ERROR" in s or "CANCEL" in s:
                raise RuntimeError(f"Kaggle run failed. Open kaggle.com/code/{slug} and check the Logs tab.")
            set_job(jid, status="Generating on Kaggle GPU")
        time.sleep(10)
    raise RuntimeError(f"Timed out waiting for Kaggle. Check kaggle.com/code/{slug}")

def run_job(jid, voice=None, text=None, resume=False):
    """Runs in a thread and owns LOCK. With resume=True the notebook is already on Kaggle, so only wait and download."""
    d = WORK / jid; d.mkdir(exist_ok=True)
    try:
        job = db.jobs.find_one({"_id": jid})
        slug = job.get("slug") or f"{KUSER}/voicelab-{jid}"
        if not resume:
            if not os.environ.get("HF_TOKEN"): raise RuntimeError("HF_TOKEN is missing from .env.")
            audio = base64.b64encode(fs.get(voice["file_id"]).read()).decode()
            payload = base64.b64encode(json.dumps({"audio": audio, "text": text, "transcript": voice["transcript"],
                                                   "hf_token": os.environ["HF_TOKEN"]}).encode()).decode()
            (d / "main.py").write_text(TEMPLATE.replace("__PAYLOAD__", payload))
            (d / "kernel-metadata.json").write_text(json.dumps({
                "id": slug, "title": f"voicelab {jid}", "code_file": "main.py", "language": "python",
                "kernel_type": "script", "is_private": "true", "enable_gpu": "true", "enable_internet": "true"}))
            set_job(jid, status="Starting Kaggle", slug=slug)
            r = kaggle("kernels", "push", "-p", str(d))
            (d / "main.py").unlink(missing_ok=True)  # contains the token; not needed after the push
            if r.returncode: raise RuntimeError(r.stdout + r.stderr)
            time.sleep(20)
        wait_for_kaggle(jid, slug, job["created"])
        set_job(jid, status="Downloading audio")
        r = kaggle("kernels", "output", slug, "-p", str(d / "out"))
        wav = d / "out" / "output.wav"
        if not wav.exists(): raise RuntimeError("No audio came back. " + r.stdout + r.stderr)
        to_mp3(wav, d / "out" / "output.mp3")
        set_job(jid, state="done", status="done")
        # Audio is saved locally, so remove the notebook (it held the HF token). Failed runs are kept for debugging.
        r = kaggle("kernels", "delete", "-y", slug)
        print("kaggle delete:", r.returncode, (r.stdout + r.stderr).strip(), flush=True)
    except Exception as e:
        set_job(jid, state="error", status="error", error=str(e))
    finally:
        LOCK.release()

def resume_jobs():  # called once at startup: pick up a clip that was running when the server stopped
    for j in db.jobs.find({"state": "running"}):
        if j.get("slug") and LOCK.acquire(blocking=False):
            threading.Thread(target=run_job, args=(j["_id"],), kwargs={"resume": True}, daemon=True).start()
        else:
            set_job(j["_id"], state="error", error="The server restarted before this clip reached Kaggle. Please generate it again.")

@app.post("/api/generate")
def generate():
    b = request.get_json(silent=True) or {}
    try: voice = db.voices.find_one({"_id": ObjectId(b.get("voice_id", ""))})
    except (InvalidId, TypeError): voice = None
    if not voice or not b.get("text", "").strip(): return jsonify(error="Choose a voice and enter text."), 400
    if not LOCK.acquire(blocking=False): return jsonify(error="A clip is already being generated. Wait for it to finish."), 409
    jid = uuid.uuid4().hex[:8]
    db.jobs.insert_one({"_id": jid, "state": "running", "status": "Queued", "created": time.time(), "updated": time.time()})
    threading.Thread(target=run_job, args=(jid, voice, b["text"].strip()[:500]), daemon=True).start()
    return jsonify(id=jid)

@app.get("/api/jobs/active")  # lets the page pick the running clip back up after a refresh
def active_job():
    j = db.jobs.find_one({"state": "running"}, sort=[("created", -1)])
    return jsonify(id=j["_id"], status=j.get("status", "")) if j else jsonify({})

@app.get("/api/jobs/<jid>")
def job(jid):
    j = db.jobs.find_one({"_id": jid})
    if not j: return jsonify(error="Unknown job."), 404
    return jsonify(state=j["state"], status=j.get("status", ""), error=j.get("error"))

@app.get("/api/jobs/<jid>/audio")
def audio(jid):
    f = WORK / jid / "out" / "output.mp3"
    if not db.jobs.find_one({"_id": jid}) or not f.exists(): return jsonify(error="No audio for this job."), 404
    return send_file(f, mimetype="audio/mpeg", conditional=True)

if __name__ == "__main__":
    resume_jobs()
    app.run(port=5000)
