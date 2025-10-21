import os, json, base64, hashlib, time
from typing import Dict, Any
from flask import Flask, request, jsonify
import requests

# ==== ENV ====
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
GOOGLE_TTS_KEY   = os.getenv("GOOGLE_TTS_KEY", "")              # <-- ใส่ใน Render
WEBHOOK_TOKEN    = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")
DEFAULT_VOICE    = os.getenv("VOICE", "th-TH-Standard-B")       # Google TTS ชายไทยชัด
DEFAULT_RATE     = float(os.getenv("VOICE_RATE", "0.92"))       # 1.0 ปกติ (0.9 ช้าลง)
DEFAULT_PITCH    = float(os.getenv("VOICE_PITCH", "-2.0"))      # นุ่มลงเล็กน้อย

SYSTEM_PROMPT = """คุณคือ Gatekeeper v2.3.1 Voice Coach (ภาษาไทยเท่านั้น)
กติกาเคร่งครัด:
- ห้ามให้คำแนะนำการเงินหรือสัญญาณซื้อขายทุกชนิด (ห้ามใช้คำว่า buy/sell/long/short/ซื้อ/ขาย/เปิดสถานะ/ปิดสถานะ)
- วิเคราะห์เชิงโครงสร้างเท่านั้น: flip / trap / momentum / BOS / volume
- ต้องระบุ: สถานะ Key Levels (W/D/S/Bonus), ตรวจ Flip Rule, ตรวจ Trap, คำเตือนความเสี่ยง
- เขียนสั้น กระชับ ชัดเจน เป็นภาษาไทยล้วน
- ปิดท้ายด้วย: 'สำหรับการศึกษาเท่านั้น'"""

app = Flask(__name__)

# ========== Utilities ==========
def safety_id(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def want_slow(pace: str) -> float:
    if pace == "slow":  return 0.90
    if pace == "fast":  return 1.05
    return DEFAULT_RATE

# ========== OpenAI (text + fallback TTS) ==========
def gpt5_analyst(msg: Dict[str, Any]) -> str:
    """
    เรียกข้อความสรุปแบบปลอดภัยจาก OpenAI (ไทยล้วน ไม่มีคำแนะนำการเงิน)
    """
    # ใช้ HTTP REST ตรง เพื่อความง่าย (ไม่พึ่งไลบรารี)
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    user = (
        f"สัญลักษณ์:{msg.get('symbol')} | TF:{msg.get('tf')}\n"
        f"close:{msg.get('close')} volume:{msg.get('volume')}\n"
        f"เขียนไทยล้วน สั้น กระชับ ตามกติกา."
    )
    body = {
        "model": "gpt-5",           # ใช้รุ่นล่าสุดในบัญชีของคุณ
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user}
        ],
        # อย่าตั้ง temperature/ฯลฯ ถ้ารุ่นไม่รองรับ
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    # เติมหมายเหตุความปลอดภัยเสมอ
    if "สำหรับการศึกษาเท่านั้น" not in text:
        text += "\n\nสำหรับการศึกษาเท่านั้น"
    return text

def tts_openai(text: str, voice_name: str = "alloy") -> bytes:
    """
    Fallback: OpenAI TTS (กรณี Google ใช้ไม่ได้)
    """
    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-4o-mini-tts",
        "voice": voice_name,
        "input": text
    }
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    return r.content  # mp3 bytes

# ========== Google TTS (REST + API Key) ==========
def tts_google_rest(text: str, voice_name: str, rate: float, pitch: float) -> bytes:
    """
    เรียก Google Cloud Text-to-Speech ผ่าน REST ด้วย API Key เดียว (ไม่ต้อง Service Account)
    """
    assert GOOGLE_TTS_KEY, "GOOGLE_TTS_KEY not set"
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    body = {
        "input": {"text": text},
        "voice": {"languageCode": "th-TH", "name": voice_name},
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": rate,  # 1.0 ปกติ; 0.9 ช้าลง
            "pitch": pitch         # -2.0 นุ่มลง
        },
    }
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "audioContent" not in data:
        raise RuntimeError(f"TTS error: {data}")
    return base64.b64decode(data["audioContent"])

# ========== Routes ==========
@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "model": "gpt-5", "voice": DEFAULT_VOICE})

@app.post("/coach_dual")
def coach_dual():
    # --- ตรวจ token ---
    token_q = request.args.get("token") or request.headers.get("X-Webhook-Token", "")
    if token_q != WEBHOOK_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # --- รับข้อมูล ---
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    # --- ทำข้อความวิเคราะห์แบบปลอดภัย ---
    try:
        text = gpt5_analyst(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": "openai_chat_failed", "detail": str(e)}), 400

    # --- เลือกเสียง/ความเร็ว ---
    pace = (request.args.get("pace") or "").lower().strip()
    rate = want_slow(pace)
    voice_param = request.args.get("voice") or DEFAULT_VOICE

    # --- สร้างเสียง (Google → fallback OpenAI) ---
    audio_bytes = b""
    audio_mime  = "audio/mpeg"
    used_voice  = voice_param
    engine      = "google-tts"

    try:
        if GOOGLE_TTS_KEY:
            audio_bytes = tts_google_rest(text, voice_param, rate, DEFAULT_PITCH)
        else:
            raise RuntimeError("GOOGLE_TTS_KEY missing")
    except Exception as e:
        # Fallback ไป OpenAI TTS (อังกฤษสำเนียงอ่านไทยได้ระดับหนึ่ง)
        try:
            audio_bytes = tts_openai(text, voice_name="fable")
            used_voice  = "fable"
            engine      = "openai-tts"
        except Exception as e2:
            return jsonify({"ok": False, "error": "tts_failed", "detail": f"{e}; {e2}"}), 400

    # --- ตอบกลับ ---
    resp = {
        "ok": True,
        "engine": engine,
        "voice": used_voice,
        "audio_mime": audio_mime,
        "text": text,
        "safety_id": safety_id({
            "ts": int(time.time()), "symbol": payload.get("symbol"), "tf": payload.get("tf")
        }),
        "audio_b64": base64.b64encode(audio_bytes).decode("utf-8"),
    }
    return jsonify(resp)

if __name__ == "__main__":
    # สำหรับรันโลคัล (Render จะใช้ gunicorn ตาม Start Command ด้านล่าง)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5053")), debug=False)
