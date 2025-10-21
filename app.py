import os, json, base64, hashlib
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError, field_validator
from openai import OpenAI

MODEL_TEXT = os.getenv("LLM_MODEL", "gpt-5")
MODEL_TTS  = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
VOICE      = os.getenv("VOICE", "alloy")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
USER_TAG   = os.getenv("USER_TAG", "kunthan-cloud-01")

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
app = Flask(__name__)

class Alert(BaseModel):
    symbol: str
    tf: str
    close: float
    volume: float
    @field_validator("symbol")
    @classmethod
    def sym_ok(cls, v):
        v = v.upper().strip()
        if not (1 <= len(v) <= 15 and v.replace(".", "").isalnum()):
            raise ValueError("invalid symbol")
        return v
    @field_validator("tf")
    @classmethod
    def tf_ok(cls, v):
        v = v.upper().strip()
        allowed = {"1M","5","15","30","1H","2H","4H","D","W","M"}
        if v not in allowed:
            raise ValueError("invalid timeframe")
        return v

def safety_identifier(user_tag: str, payload: dict) -> str:
    h = hashlib.sha256()
    h.update((user_tag + "::" + json.dumps(payload, sort_keys=True)).encode())
    return h.hexdigest()

SYSTEM_PROMPT = """You are Gatekeeper v2.3.1 Voice Coach.
Strict rules:
- NO financial advice. Do NOT say buy/sell/open/close/enter/exit/long/short.
- Objective, structural analysis only: flip / trap / momentum / BOS / volume.
- Always include: Key Levels status (W/D/S/Bonus), Flip rule check, Trap check, Risk warning.
- Format: [TH paragraph], then [EN single paragraph].
- End with: 'สำหรับการศึกษาเท่านั้น / For educational purposes only.'"""

def build_user_prompt(a: Alert) -> str:
    return (
        f"Symbol: {a.symbol}\n"
        f"TF: {a.tf}\n"
        f"Close: {a.close}\n"
        f"Volume: {a.volume}\n"
        "Locked Key Levels (EL preset): W:100.00 | D:105.45/94.95 | S:101.95/96.13 | Bonus:87.84.\n"
        "Flip if close >= 105.45 (confirmed); never reuse broken resistance; trap zone ~99-101.\n"
    )

@app.get("/healthz")
def healthz():
    return jsonify(ok=True, model=MODEL_TEXT, voice=VOICE)

@app.post("/coach_dual")
def coach_dual():
    if WEBHOOK_TOKEN and request.headers.get("X-Webhook-Token") != WEBHOOK_TOKEN:
        return jsonify(error="unauthorized"), 401
    try:
        data = request.get_json(force=True, silent=False)
        alert = Alert(**data)
    except (TypeError, ValidationError) as e:
        return jsonify(error="bad_payload", detail=str(e)), 400

    sid = safety_identifier(USER_TAG, data)

    try:
        chat = client.chat.completions.create(
            model=MODEL_TEXT,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + f"\n[safety_identifier:{sid}]"},
                {"role": "user", "content": build_user_prompt(alert)}
            ],
        )
        text = chat.choices[0].message.content.strip()
    except Exception as e:
        return jsonify(error="openai_chat_failed", detail=str(e)), 502

    blocked = ["buy","sell","enter","exit","long","short","ซื้อ","ขาย","เปิดสถานะ","ปิดสถานะ"]
    if any(b in text.lower() for b in blocked):
        text = ("คำอธิบายถูกปรับเพื่อความปลอดภัยเชิงนโยบาย: ให้ข้อมูลเชิงโครงสร้างเท่านั้น "
                "(flip/trap/volume). สำหรับการศึกษาเท่านั้น / For educational purposes only.")

    audio_b64 = None
    try:
        speech = client.audio.speech.create(model=MODEL_TTS, voice=VOICE, input=text)
        audio_bytes = speech.read()
        audio_b64 = base64.b64encode(audio_bytes).decode()
    except Exception:
        audio_b64 = None

    return jsonify(ok=True, safety_id=sid, text=text,
                   audio_b64=audio_b64, audio_mime="audio/mpeg"), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
