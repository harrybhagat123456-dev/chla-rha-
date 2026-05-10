import asyncio
import os
from flask import Flask, render_template_string, request, jsonify
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, BadRequest
)

app = Flask(__name__)
app.secret_key = os.urandom(24)

pending_clients = {}

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pyrogram Session Generator</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', sans-serif;
    background: #0f0f1a;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .card {
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 16px;
    padding: 40px;
    width: 100%;
    max-width: 480px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  h1 { font-size: 22px; margin-bottom: 6px; color: #7c8ff8; }
  .subtitle { font-size: 13px; color: #666; margin-bottom: 28px; }
  label {
    display: block;
    font-size: 12px;
    color: #888;
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  input {
    width: 100%;
    background: #0f0f1a;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    color: #e0e0e0;
    padding: 12px 14px;
    font-size: 14px;
    margin-bottom: 18px;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus { border-color: #7c8ff8; }
  input:read-only {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .prefilled-badge {
    display: inline-block;
    font-size: 10px;
    background: #1a3a1a;
    color: #7fff7f;
    border: 1px solid #2a5a2a;
    border-radius: 4px;
    padding: 2px 7px;
    margin-left: 8px;
    vertical-align: middle;
    letter-spacing: 0.3px;
    text-transform: none;
  }
  button {
    width: 100%;
    background: #7c8ff8;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 13px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  button:hover { background: #6070e0; }
  button:disabled { background: #3a3a5a; cursor: not-allowed; }
  .step { display: none; }
  .step.active { display: block; }
  .result-box {
    background: #0f0f1a;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    padding: 14px;
    font-family: monospace;
    font-size: 11px;
    word-break: break-all;
    color: #7fff7f;
    margin-bottom: 16px;
    max-height: 140px;
    overflow-y: auto;
  }
  .copy-btn {
    background: #1a3a2a;
    color: #7fff7f;
    border: 1px solid #2a5a3a;
  }
  .copy-btn:hover { background: #2a4a3a; }
  .set-secret-btn {
    background: #7c8ff8;
    margin-top: 10px;
  }
  .error {
    color: #ff6b6b;
    font-size: 13px;
    margin-bottom: 14px;
    padding: 10px;
    background: #2a1a1a;
    border-radius: 6px;
    border: 1px solid #5a2a2a;
  }
  .success-banner {
    color: #7fff7f;
    font-size: 13px;
    margin-bottom: 14px;
    padding: 10px;
    background: #1a2a1a;
    border-radius: 6px;
    border: 1px solid #2a5a2a;
    display: none;
  }
  .spinner {
    display: inline-block;
    width: 16px; height: 16px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .note { font-size: 12px; color: #666; margin-top: 12px; }
  a { color: #7c8ff8; }
</style>
</head>
<body>
<div class="card">
  <h1>🔑 Session Generator</h1>
  <p class="subtitle">Generate your Pyrogram string session for the bot</p>

  <div id="error-box" class="error" style="display:none;"></div>
  <div id="success-box" class="success-banner"></div>

  <!-- Step 1: Phone only (API creds pre-filled) -->
  <div class="step active" id="step1">
    {% if api_id %}
    <label>API ID <span class="prefilled-badge">✓ pre-filled</span></label>
    <input type="text" id="api_id" value="{{ api_id }}" readonly>
    <label>API HASH <span class="prefilled-badge">✓ pre-filled</span></label>
    <input type="text" id="api_hash" value="{{ api_hash }}" readonly>
    {% else %}
    <label>API ID</label>
    <input type="number" id="api_id" placeholder="12345678">
    <label>API HASH</label>
    <input type="text" id="api_hash" placeholder="abcdef1234567890abcdef1234567890">
    {% endif %}
    <label>Your Phone Number (with country code)</label>
    <input type="text" id="phone" placeholder="+1234567890" autofocus>
    <button onclick="sendCode(event)">Send OTP to Telegram</button>
    {% if not api_id %}
    <p class="note">Get your API credentials at <a href="https://my.telegram.org" target="_blank">my.telegram.org</a></p>
    {% endif %}
  </div>

  <!-- Step 2: OTP -->
  <div class="step" id="step2">
    <label>OTP Code sent to your Telegram</label>
    <input type="text" id="otp" placeholder="1 2 3 4 5" maxlength="10" autofocus>
    <button onclick="verifyOtp(event)">Verify Code</button>
    <p class="note">Check your Telegram app or SMS for the login code.</p>
  </div>

  <!-- Step 2b: 2FA Password -->
  <div class="step" id="step2b">
    <label>Two-Factor Authentication Password</label>
    <input type="password" id="password" placeholder="Your 2FA password" autofocus>
    <button onclick="verify2fa(event)">Submit Password</button>
  </div>

  <!-- Step 3: Done -->
  <div class="step" id="step3">
    <label>Your Session String</label>
    <div class="result-box" id="session-result"></div>
    <button class="copy-btn" onclick="copySession(event)">📋 Copy Session String</button>
    <button class="set-secret-btn" onclick="saveSecret(event)">💾 Save as SESSION Secret</button>
    <p class="note" style="margin-top:14px;">⚠️ Keep this string private — it grants full access to your Telegram account.</p>
  </div>
</div>

<script>
let sessionId = null;

function showError(msg) {
  const box = document.getElementById('error-box');
  box.textContent = msg;
  box.style.display = 'block';
  document.getElementById('success-box').style.display = 'none';
}
function showSuccess(msg) {
  const box = document.getElementById('success-box');
  box.textContent = msg;
  box.style.display = 'block';
  document.getElementById('error-box').style.display = 'none';
}
function clearMessages() {
  document.getElementById('error-box').style.display = 'none';
  document.getElementById('success-box').style.display = 'none';
}
function goStep(n) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.getElementById('step' + n).classList.add('active');
  clearMessages();
  const step = document.getElementById('step' + n);
  const inp = step.querySelector('input:not([readonly])');
  if (inp) setTimeout(() => inp.focus(), 100);
}
function setLoading(btn, loading, label) {
  btn.disabled = loading;
  btn.innerHTML = loading ? '<span class="spinner"></span>Please wait...' : label;
}

async function sendCode(e) {
  const btn = e.target;
  const label = btn.innerHTML;
  const api_id = document.getElementById('api_id').value.trim();
  const api_hash = document.getElementById('api_hash').value.trim();
  const phone = document.getElementById('phone').value.trim();
  if (!api_id || !api_hash || !phone) return showError('All fields are required.');
  clearMessages();
  setLoading(btn, true, label);
  try {
    const res = await fetch('/send_code', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ api_id, api_hash, phone })
    });
    const data = await res.json();
    if (data.ok) { sessionId = data.session_id; goStep(2); }
    else showError(data.error || 'Failed to send code.');
  } catch(err) { showError('Network error: ' + err.message); }
  setLoading(btn, false, label);
}

async function verifyOtp(e) {
  const btn = e.target;
  const label = btn.innerHTML;
  const otp = document.getElementById('otp').value.trim().replace(/\s/g, '');
  if (!otp) return showError('Enter the OTP code.');
  clearMessages();
  setLoading(btn, true, label);
  try {
    const res = await fetch('/verify_code', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sessionId, otp })
    });
    const data = await res.json();
    if (data.ok && data.session_string) {
      document.getElementById('session-result').textContent = data.session_string;
      goStep(3);
    } else if (data.need_2fa) {
      goStep('2b');
    } else {
      showError(data.error || 'Invalid code.');
    }
  } catch(err) { showError('Network error: ' + err.message); }
  setLoading(btn, false, label);
}

async function verify2fa(e) {
  const btn = e.target;
  const label = btn.innerHTML;
  const password = document.getElementById('password').value;
  if (!password) return showError('Enter your 2FA password.');
  clearMessages();
  setLoading(btn, true, label);
  try {
    const res = await fetch('/verify_2fa', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sessionId, password })
    });
    const data = await res.json();
    if (data.ok && data.session_string) {
      document.getElementById('session-result').textContent = data.session_string;
      goStep(3);
    } else {
      showError(data.error || 'Wrong password.');
    }
  } catch(err) { showError('Network error: ' + err.message); }
  setLoading(btn, false, label);
}

function copySession(e) {
  const text = document.getElementById('session-result').textContent;
  navigator.clipboard.writeText(text).then(() => {
    const btn = e.target;
    const orig = btn.textContent;
    btn.textContent = '✅ Copied!';
    setTimeout(() => btn.textContent = orig, 2000);
  });
}

async function saveSecret(e) {
  const btn = e.target;
  const label = btn.innerHTML;
  const session_string = document.getElementById('session-result').textContent;
  setLoading(btn, true, label);
  try {
    const res = await fetch('/save_session', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_string })
    });
    const data = await res.json();
    if (data.ok) {
      showSuccess('✅ SESSION secret saved! You can now start the bot.');
      btn.disabled = true;
      btn.textContent = '✅ Saved!';
    } else {
      showError(data.error || 'Failed to save.');
      setLoading(btn, false, label);
    }
  } catch(err) {
    showError('Network error: ' + err.message);
    setLoading(btn, false, label);
  }
}
</script>
</body>
</html>
"""

loop = asyncio.new_event_loop()

def run_async(coro):
    return loop.run_until_complete(coro)

@app.route("/")
def index():
    api_id = os.environ.get("API_ID", "")
    api_hash = os.environ.get("API_HASH", "")
    return render_template_string(HTML, api_id=api_id, api_hash=api_hash)

@app.route("/send_code", methods=["POST"])
def send_code():
    data = request.json
    try:
        api_id = int(data["api_id"])
    except (ValueError, KeyError):
        return jsonify({"ok": False, "error": "Invalid API ID."})
    api_hash = data.get("api_hash", "").strip()
    phone = data.get("phone", "").strip()
    if not api_hash or not phone:
        return jsonify({"ok": False, "error": "Missing fields."})

    session_id = f"sess_{api_id}_{phone.replace('+','').replace(' ','')}"

    async def _send():
        client = Client(session_id, api_id=api_id, api_hash=api_hash, in_memory=True)
        await client.connect()
        sent = await client.send_code(phone)
        pending_clients[session_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash
        }
        return session_id

    try:
        sid = run_async(_send())
        return jsonify({"ok": True, "session_id": sid})
    except PhoneNumberInvalid:
        return jsonify({"ok": False, "error": "Invalid phone number. Include country code e.g. +91xxxxxxxxxx"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/verify_code", methods=["POST"])
def verify_code():
    data = request.json
    session_id = data.get("session_id")
    otp = data.get("otp", "").replace(" ", "")
    info = pending_clients.get(session_id)
    if not info:
        return jsonify({"ok": False, "error": "Session expired. Please start over."})

    async def _verify():
        client = info["client"]
        await client.sign_in(info["phone"], info["phone_code_hash"], otp)
        session_string = await client.export_session_string()
        await client.disconnect()
        del pending_clients[session_id]
        return session_string

    try:
        ss = run_async(_verify())
        return jsonify({"ok": True, "session_string": ss})
    except SessionPasswordNeeded:
        return jsonify({"ok": True, "need_2fa": True})
    except PhoneCodeInvalid:
        return jsonify({"ok": False, "error": "Invalid OTP code. Please check and try again."})
    except PhoneCodeExpired:
        return jsonify({"ok": False, "error": "OTP expired. Please start over."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/verify_2fa", methods=["POST"])
def verify_2fa():
    data = request.json
    session_id = data.get("session_id")
    password = data.get("password", "")
    info = pending_clients.get(session_id)
    if not info:
        return jsonify({"ok": False, "error": "Session expired. Please start over."})

    async def _2fa():
        client = info["client"]
        await client.check_password(password)
        session_string = await client.export_session_string()
        await client.disconnect()
        del pending_clients[session_id]
        return session_string

    try:
        ss = run_async(_2fa())
        return jsonify({"ok": True, "session_string": ss})
    except BadRequest:
        return jsonify({"ok": False, "error": "Wrong 2FA password."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/save_session", methods=["POST"])
def save_session():
    data = request.json
    session_string = data.get("session_string", "").strip()
    if not session_string:
        return jsonify({"ok": False, "error": "No session string provided."})
    try:
        import subprocess
        env = os.environ.copy()
        env["SESSION"] = session_string
        with open(".session_string.txt", "w") as f:
            f.write(session_string)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
