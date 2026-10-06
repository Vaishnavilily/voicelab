# VoiceLab: Telugu voice cloning with IndicF5 on Kaggle

A local web page for cloning a voice from a short recording and generating Telugu speech.
The page and server run on your own PC. Each clip is generated on a free Kaggle GPU.

```
Browser (React) -> Flask server on your PC -> Kaggle API -> Kaggle GPU notebook -> audio back to your PC
```

Each clip takes about 3 minutes. The server creates a private Kaggle notebook for the clip,
downloads `output.wav` when it finishes, and then deletes the notebook.

## What you need (one-time)
1. **Python 3.11+**, **Node 18+** and **ffmpeg** installed. Check with `python --version`, `node --version`, `ffmpeg -version`.
2. **A Kaggle account with a verified phone number.** Without it, Kaggle refuses Internet and GPU for notebooks.
3. **A Kaggle API token:** kaggle.com/settings/api, "Generate New Token".
4. **A Hugging Face read token:** huggingface.co/settings/tokens. Also open the IndicF5 model page
   (huggingface.co/ai4bharat/IndicF5) and accept its terms.
5. **A free MongoDB Atlas cluster:** create a database user (letters and numbers only in the password)
   and allow your IP under Network Access. Copy the connection string.

## Set up
1. Copy `.env.example` to `.env` (the file name starts with a dot) and fill in every value:
   ```
   MONGODB_URI=mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DB=voicelab
   KAGGLE_USERNAME=your-exact-kaggle-username
   KAGGLE_API_TOKEN=your-kaggle-token
   HF_TOKEN=your-huggingface-token
   ```
   No spaces around `=` and no quotes. `KAGGLE_USERNAME` is the part of your notebook address after
   `kaggle.com/code/`, so copy it exactly.
2. Start the server:
   ```
   cd backend
   python -m pip install -r requirements.txt
   python app.py
   ```
3. In a second terminal, start the page:
   ```
   cd frontend
   npm install
   npm run dev
   ```
4. Open the address it prints (usually http://localhost:5173). Keep both terminals open.

Restart the server after any change to `.env`. It only reads the file at startup.

## Use it
1. **Voices tab:** click "Add cloned voice". Record or upload 5-10 seconds of one clean voice, and type exactly
   what is said in it, word for word. The transcript must match the audio or the output will be poor.
2. **Speech tab:** choose the voice, enter Telugu text (up to 500 characters), click Generate. Keep the tab open.
   One clip can be generated at a time.

## Keep your keys safe
- Never share or commit `.env`. It is listed in `.gitignore`.
- Before zipping or uploading the project, delete `.env`, `backend/jobs/` and `node_modules/`.
- Each person should use their own Kaggle, Hugging Face and MongoDB accounts.
- The Hugging Face token travels to Kaggle inside the job, in a private notebook that is deleted after a successful run.
  If a run fails, the notebook stays so you can read its log. Delete it yourself on Kaggle (Your Work) when you are done.
- In Atlas, avoid leaving "allow access from anywhere" (0.0.0.0/0) on for long. Remove it or limit it to your own IP.

## Troubleshooting
| What you see | What it means |
|---|---|
| `ModuleNotFoundError` when starting the server | Run `python -m pip install -r requirements.txt` in `backend` |
| `KeyError: 'MONGODB_URI'` (or another name) | `.env` is missing, misnamed (`.env.txt`), or not in the project folder next to `backend` and `frontend` |
| `bad auth : Authentication failed` | Wrong database username or password in `MONGODB_URI`. Use a database user, not your Atlas login |
| Page says "Something went wrong" | Read the traceback in the server terminal. The last line is the cause |
| `Permission 'kernelSessions.enableInternet' was denied` or "name resolution" errors in the Kaggle log | Verify your phone number on Kaggle |
| `Permission 'kernels.get' was denied` in the server terminal | `KAGGLE_USERNAME` is spelled wrong |
| `409 Conflict` from Kaggle | An old notebook with the same name is stuck. Delete it on Kaggle (Your Work) and retry |
| Page stays on "Generating" for over 25 minutes | Open the newest notebook on Kaggle and check its Logs tab |
| Audio sounds garbled | The transcript does not match the sample, or the sample is noisy or longer than 10 seconds |
