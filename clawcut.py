#!/usr/bin/env python3
"""
ClawCut - MLX Edition (Ollama Compatibility Fix) - V1
Translates OpenAI streams from MLX to Ollama dialect for OpenClaw.
Optimized for LLM on Apple Silicon with performance tracking.
"""

from flask import Flask, request, Response
import requests
import json
import re
import sys
import time
import logging
from datetime import datetime, timezone

# Disable Flask (Werkzeug) default logging for a clean stream output
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# ==========================================
# CONFIGURATION
# ==========================================
MAC_IP = "192.xxx.xxx.xxx" 
MLX_SERVER_URL = f"http://{MAC_IP}:8080/v1/chat/completions"
# Your Model in Open Claw openclaw.json:  e.g. ollama/qwen2.5:14b
OPENCLAW_MODEL_ID = "" 
# Your MLX_MODEL_IDENTIFIER:  e.g. mlx-community/Qwen2.5-14B-Instruct-4bit
MLX_MODEL_IDENTIFIER = ""

# DEBUG_MODE: Set to True to log full payloads and raw communication
DEBUG_MODE = False 
# ==========================================

@app.route('/api/chat', methods=['POST'])
@app.route('/v1/api/chat', methods=['POST'])
def proxy():
    start_time = time.time()
    try:
        ollama_data = request.json
        if not ollama_data:
            return json.dumps({"error": "No data received"}), 400
            
        messages = ollama_data.get('messages', [])
        
        print(f"\n\n[DEBUG] {'='*40}")
        print(f"[DEBUG] REQUEST RECEIVED: {datetime.now().strftime('%H:%M:%S')}")

        if DEBUG_MODE:
            print(f"[DEBUG] FULL INCOMING PAYLOAD:\n{json.dumps(ollama_data, indent=2)}")

        # KV-Cache Trick: Filter dynamic timestamps to maximize hardware cache hits on Mac
        if messages and messages[0]['role'] == 'system':
            sys_msg = messages[0]['content']
            dynamic_pattern = r"(?i)(The current date and time is.*?$|Current Time:.*?$|Date:.*?$)"
            dynamic_parts = re.findall(dynamic_pattern, sys_msg, flags=re.MULTILINE)

            if dynamic_parts:
                messages[0]['content'] = re.sub(dynamic_pattern, "", sys_msg, flags=re.MULTILINE).strip()
                if len(messages) > 1 and messages[-1]['role'] == 'user':
                    time_context = "\n\n[System-Info: " + " | ".join(dynamic_parts) + "]"
                    messages[-1]['content'] += time_context

        openai_payload = {
            "model": MLX_MODEL_IDENTIFIER,
            "messages": messages,
            "stream": True,
            "temperature": ollama_data.get('options', {}).get('temperature', 0.7),
            "max_tokens": ollama_data.get('options', {}).get('num_predict', 4096)
        }

        if DEBUG_MODE:
            print(f"[DEBUG] FULL OUTGOING PAYLOAD TO MLX:\n{json.dumps(openai_payload, indent=2)}")

        print(f"[DEBUG] Sending payload to Mac... (Waiting for prefill)")
        req = requests.post(MLX_SERVER_URL, json=openai_payload, stream=True, timeout=600)
        
        if req.status_code != 200:
            error_detail = req.text
            print(f"[ERROR] Mac Error {req.status_code}: {error_detail}")
            return json.dumps({"error": f"Mac MLX Error {req.status_code}", "details": error_detail}), 502

        def generate():
            first_token_received = False
            token_count = 0
            prefill_time = 0
            full_content = ""
            
            for line in req.iter_lines():
                if not line:
                    continue
                
                line_text = line.decode('utf-8')
                
                if DEBUG_MODE:
                    print(f"[DEBUG] RAW LINE FROM MLX: {line_text}")

                if line_text.startswith("data: "):
                    data_str = line_text[6:].strip()
                    
                    if data_str == "[DONE]":
                        end_time = time.time()
                        duration = end_time - start_time
                        gen_duration = max(0.1, duration - prefill_time)
                        
                        # Check for empty or silent responses
                        if not full_content.strip() or "NO_REPLY" in full_content:
                            print(f"\n[DEBUG] WARNING: Model generated no usable response (Silent Mode).")
                        
                        print(f"\n[DEBUG] {'-'*40}")
                        print(f"[DEBUG] Finished in {duration:.2f}s | Tokens: {token_count} | Speed: {token_count/gen_duration:.1f} t/s")
                        break
                    
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk['choices'][0]['delta']
                        content = delta.get('content', '')
                        
                        if not first_token_received:
                            prefill_time = time.time() - start_time
                            print(f"[DEBUG] First token received after {prefill_time:.2f}s.")
                            print(f"[DEBUG] --- START STREAM ---")
                            first_token_received = True
                        
                        if content:
                            token_count += 1
                            full_content += content
                            sys.stdout.write(content)
                            sys.stdout.flush()

                            ollama_chunk = {
                                "model": OPENCLAW_MODEL_ID,
                                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                "message": {"role": "assistant", "content": content},
                                "done": False
                            }
                            yield json.dumps(ollama_chunk).encode('utf-8') + b'\n'
                    except Exception as e:
                        if DEBUG_MODE:
                            print(f"[DEBUG] JSON Parse Error: {e}")
                        continue
            
            yield json.dumps({
                "model": OPENCLAW_MODEL_ID,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "message": {"role": "assistant", "content": ""},
                "done": True
            }).encode('utf-8') + b'\n'

        return Response(generate(), content_type='application/x-ndjson')

    except Exception as e:
        print(f"\n[ERROR] Proxy error: {e}")
        return json.dumps({"error": str(e)}), 502

if __name__ == '__main__':
    print(f"==========================================")
    print(f"ClawCut MLX Bridge ACTIVE")
    print(f"Flask logs disabled for clean stream.")
    if DEBUG_MODE:
        print(f"VERBOSE DEBUG MODE: ON")
    print(f"==========================================")
    # Coupled Flask debug mode to internal DEBUG_MODE
    # Note: Use_reloader=False prevents the script from starting twice in the terminal
    app.run(host='0.0.0.0', port=5000, debug=DEBUG_MODE, use_reloader=False)
