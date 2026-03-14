#!/usr/bin/env python3
"""
ClawCut - Universal LLM Bridge & Proxy (BETA) - v. 3.1.9
--------------------------------------------------------------------------------
LICENSE: ClawCut Personal & Non-Commercial License
Copyright (c) 2026 Niels Gerhardt
https://github.com/back-me-up-scotty/ClawCut

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files, to use, copy, modify, 
and share the software for PERSONAL and NON-COMMERCIAL purposes only, 
subject to the following conditions:

1. The above copyright notice and this permission notice shall be included in 
   all copies or substantial portions of the software.
2. COMMERCIAL USE, sale, or distribution for profit is STRICTLY PROHIBITED.
3. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

There is no guarantee that ClawCut will work in every 
configuration or with future OpenClaw updates. It is very likely that 
this is not the case. ClawCut therefore depends on the community for 
its development. Make it your own. Share your results.
--------------------------------------------------------------------------------

ClawCut is an experimental project to manipulate, inject JSON-Calls and 
extract JSON clutter from OpenClaw. Why all this? Because smaller models tend to get 
confused by all the JSON clutter and, in particular, can no longer handle 
tool calls — if they are even capable of doing so in the first place.

Features like 'SMART_AMNESIA' and 'PROMPT_TRIMMING' are designed to keep small 
models stable by cutting out "noise". This means the model MAY LOSE awareness 
of previous parts of the conversation. If you need a model to remember 
long-term details within a single session, these features might interfere.

PURPOSE OF THIS PROXY:
OpenClaw is a powerful framework that, by default, sends massive system prompts 
(often >28,000 characters) and complex tool definitions (JSON tools) to the LLM. 
While large cloud models or high-end local models (14B etc.) handle this well, 
small models (7B, 8B) running on limited hardware (Mac/MLX or Raspberry Pi) 
often suffer from "Cognitive Overload":
- Extreme processing latency (slow Time To First Token).
- Forgetting their identity or available tools.
- Hallucinating text answers instead of executing local scripts.
- Connection timeouts or malformed JSON responses.
- Huge RAM consumption

This proxy acts as a "Man-in-the-Middle" between OpenClaw and your local LLM 
server to optimize the data flow:

1. PROMPT TRIMMING: Automatically removes unused default skills from the system 
   prompt to keep the context window small and focused.
2. SMART AMNESIA: Intelligently truncates chat history after successful tool 
   executions to free up "mental space" for the model.
3. ATTENTION FORCER: Injects a reminder at the very end of the user query to 
   ensure the model prioritizes tool usage.
4. TOOL FORCER: Injects keywords for tool calling and points to commands.
5. INPUT RESCUE: Short-circuits known incoming requests (like Cron-Jobs) to 
   bypass LLM latency and ensure 100% reliability for automated tasks.
6. BASH-RESCUE: Detects poorly formatted script calls (e.g., naked code blocks) 
   and converts them into valid OpenClaw tool calls on the fly.

BENEFITS:
- Significantly faster response times (TTFT), as the model has much less text 
  to process upfront.
- Improved reliability when using and calling scripts (bash or whatever).
- Robust error handling for stream interruptions or formatting errors.

WHEN TO USE:
- Ideal for small models (7B-8B) running on hardware like Mac (MLX), Windows 
  or Linux.
- If your model "chats" too much instead of executing commands.

WHEN NOT TO USE:
- If you are using highly intelligent, large models (14B+) that can handle 
  complex prompts natively. In this case, the proxy can act purely as a logger 
  and format translator without manipulating the content if 
  PASS_THROUGH_MODE = True.

HOW TO START:
- See README.md on GitHub. https://github.com/back-me-up-scotty/ClawCut

--KNOWN ISSUES -----------------------------------------------------------------
- Since OpenClaw version 2026.3.12 there are issues with the routing of messages 
  triggered by a cron job. ClawCut clearly sees this messages. The issue seems 
  to be on OpenClaw's side. FORCE_CRON_DELIVERY has unfortunately no effect. 
  OpenClaw ignores it.
--------------------------------------------------------------------------------
"""

from flask import Flask, request, Response
import requests
import json
import re
import sys
import os
import time
import logging
import subprocess
import signal
from datetime import datetime, timezone

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# ==========================================
# CONFIGURATION PROFILES
# ==========================================
# Note for OpenClaw Configuration (e.g., openclaw.json or openclaw.conf):
# When using this proxy, the specific model name you configure in OpenClaw does NOT matter.
# The proxy intercepts the traffic and completely overrides the requested model 
# based on the selected profiles below. 
# You only need to ensure your OpenClaw provider URL points to the proxy: 
# "http://127.0.0.1:5000/v1"

PROFILES = {
    "LLM1": {
        "ip": "192.168.0.xxx",
        "port": 8090,
        "model_id": "ollama/Qwen2.5-Coder-7B-Instruct-4bit",
        "model_name": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    },
    "LLM2": {
        "ip": "192.168.0.xxx",
        "port": 11434,
        "model_id": "ollama/qwen2.5:14b",
        "model_name": "qwen2.5:14b"
    }
}

# Default Profile (if no flag is provided)
SELECTED_PROFILE = "LLM1"

# Parse Profile from command line
# Use the '-LLM1' or '-LLM2' flag when starting the proxy to switch server configurations.
# Example: python clawcut-mlx.py -LLM2
if "-LLM1" in sys.argv:
    SELECTED_PROFILE = "LLM1"
elif "-LLM2" in sys.argv:
    SELECTED_PROFILE = "LLM2"

cfg = PROFILES[SELECTED_PROFILE]

# Active Server Config
LLM_SERVER_URL = f"http://{cfg['ip']}:{cfg['port']}/v1/chat/completions"
OPENCLAW_MODEL_ID = cfg['model_id']
LLM_MODEL_IDENTIFIER = cfg['model_name']

# Logging & Storage Config
# DEBUG_MODE = True prints the full JSON payloads to the console (useful for troubleshooting).
# WRITE_TO_LOGFILE saves the terminal output to the specified PATH_TO_LOGFILE.
# DELETE_LOG_SIZE rotates/deletes the log automatically when it reaches this size to prevent disk full issues.
DEBUG_MODE = True
WRITE_TO_LOGFILE = True
PATH_TO_LOGFILE = '/home/user/clawcut.log' # Change to your preferred log path
DELETE_LOG_SIZE = '10 MB'

# --- SMART AMNESIA MODE ---
# Over time, chat histories get too long for small models to process efficiently.
# If True, the proxy watches for tool calls (specifically the 'exec' tool). 
# Once a tool has been successfully executed, the proxy truncates all chat history 
# prior to that execution. This creates a "fresh start" while keeping the final results,
# preventing infinite loops and keeping the context window small.
ENABLE_SMART_AMNESIA = True

# Amnesia only when the current turn processes a tool result (last message is ‘tool’)
# Example: You exchanged 5 messages without calling a tool. The context is preserved. 
# Then you call a tool (exec). Result: The context is cut off, and you can no longer 
# retrieve the conversation that took place before the tool was called. 
#
# CHAT_HISTORY_LIMIT lets you specify how much chat history (recent messages in a normal chat) 
# should be retained.
CHAT_HISTORY_LIMIT = 10  # Number of messages (excluding system messages) in chat mode

# --- UNIVERSAL AUTO-DELIVERY ---
# Legacy Support: Before OpenClaw 3.12, the proxy had to manually force the LLM 
# to send its text answers to WhatsApp using the 'message' tool.
# For OpenClaw 3.12+, keep this FALSE. OpenClaw now has "native reply routing" 
# and will automatically route text answers back to the chat interface.
# Setting this to True on modern OpenClaw versions will cause a "Message failed" conflict.
FORCE_AUTO_DELIVERY = False

# Automatically force text delivery to WhatsApp if the request originated from a Cron job.
# Cron jobs lack a native chat interface, so OpenClaw's native routing won't show the text anywhere.
FORCE_CRON_DELIVERY = False
AUTO_DELIVERY_CHANNEL = "whatsapp"  
AUTO_DELIVERY_TARGET = "+49123456" 


# ==========================================
# --- PROXY BEHAVIOR ---
# ==========================================

# Pure Pass-Through Mode: If True, completely disables all proxy logic (trimming, amnesia, auto-delivery, bash-rescue).
# The proxy will only log traffic and forward the exact JSON between OpenClaw and the LLM, maintaining format compatibility.
# Useful for powerful models (e.g., 14B, 70B, GPT-4) that don't need workarounds.
#
# If passthrough mode is active (True), you'll immediately notice a difference in speed. Responses from the model are 
# generated much more slowly, and tool execution on small models will likely no longer work because the proxy no longer injects tool calls,
# and the model becomes overwhelmed again by the massive increase in JSON clutter.
# 
PASS_THROUGH_MODE = False  #Set "False" to unleash ClawCuts power

# BASE PATH FOR SCRIPT RESCUE
# Change this to match the root directory where your scripts (if you have some) are stored, that OpenClaw should execute.
# This matches what you tell the LLM for example in your TOOLS.md. See also EMERGENCY_RESCUES.
EXPECTED_SCRIPT_BASE_PATH = "/home/user/"

# Default message sent to the user when an audio file is delivered
AUDIO_DELIVERY_MESSAGE = "Here is your audio."

# 1. System Prompt Trimming (Cognitive Overload Protection)
# If True, the proxy aggressively strips out the skills listed in TRIM_SKILLS before sending 
# the prompt to the model, freeing up its attention span for your custom tools.
ENABLE_PROMPT_TRIMMING = True
TRIM_SKILLS = [
    "clawhub", "gemini", "gh-issues", "github", "healthcheck", 
    "nano-pdf", "openai-whisper", "skill-creator", "summarize", 
    "video-frames", "wacli", "weather"
]

# 2. Attention Forcer (End-of-Prompt Injection)
# If True, this injects a strong reminder at the very end of the user's latest message.
ENABLE_ATTENTION_FORCER = True
ATTENTION_FORCER_TEXT = "\n\n[SYSTEM-REMINDER: NEVER respond to requests for local scripts, data, or services directly with text! You MUST use the ‘exec’ tool FIRST!]"

# 3. Emergency Rescue (Catch & Convert) - Where the tool call magic happens
# Intercepts specific model texts and converts them into hidden 'exec' tool calls.
# Useful if the model only describes what it wants to do, but forgets to output the actual JSON tool call.
# If ENABLE_INPUT_RESCUE is True, this also triggers for incoming user requests (e.g. Cron jobs).
#
# Scripts down below are examples how to use. These are my own script I want OpenClaw to call. Change to
# your scripts if you have some and set ENABLE_EMERGENCY_RESCUE = True
ENABLE_EMERGENCY_RESCUE = True
ENABLE_INPUT_RESCUE = True
EMERGENCY_RESCUES = [
    {
        "keywords": ["weather", "tell"], 
        "command": 'bash /home/user/weather.sh "New York"'
    },
    {
        "keywords": ["diesel", "price"], 
        "command": 'bash /home/user/.openclaw/workspace/skills/diesel-price/diesel_price.sh'
    },
     {
        "keywords": ["backup", "create"], 
        "command": 'bash /home/user/.openclaw/workspace/skills/system_control/run_bmus.sh'
    }
]
# ==========================================

def _parse_size_string(size_str):
    size_str = size_str.strip().upper()
    try:
        if size_str.endswith('MB'): return int(float(size_str.replace('MB', '').strip()) * 1024 * 1024)
        elif size_str.endswith('KB'): return int(float(size_str.replace('KB', '').strip()) * 1024)
        elif size_str.endswith('GB'): return int(float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024)
        elif size_str.endswith('B'): return int(float(size_str.replace('B', '').strip()))
        else: return int(size_str)
    except ValueError:
        return 10 * 1024 * 1024

class DualLogger:
    def __init__(self, filepath, max_size_str):
        self.terminal = sys.stdout
        self.filepath = filepath
        self.max_bytes = _parse_size_string(max_size_str)

    def _check_size_and_rotate(self):
        if os.path.exists(self.filepath) and os.path.getsize(self.filepath) >= self.max_bytes:
            try: os.remove(self.filepath)
            except OSError: pass

    def write(self, message):
        self.terminal.write(message)
        if WRITE_TO_LOGFILE:
            self._check_size_and_rotate()
            try:
                with open(self.filepath, "a", encoding="utf-8") as log_file:
                    log_file.write(message)
            except IOError: pass

    def flush(self):
        self.terminal.flush()

if WRITE_TO_LOGFILE:
    sys.stdout = DualLogger(PATH_TO_LOGFILE, DELETE_LOG_SIZE)
    sys.stderr = sys.stdout

def kill_other_instances():
    current_pid = os.getpid()
    script_name = os.path.basename(__file__)
    try:
        pids = subprocess.check_output(['pgrep', '-f', script_name]).decode().split()
        killed_any = False
        for pid_str in pids:
            try:
                pid = int(pid_str)
                if pid != current_pid:
                    os.kill(pid, signal.SIGTERM)
                    print(f"[SYSTEM] Old background process (PID {pid}) terminated.")
                    killed_any = True
            except Exception:
                pass
        if killed_any:
            time.sleep(2) 
    except subprocess.CalledProcessError:
        pass

def extract_hallucinated_tools(text):
    jsons = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, char in enumerate(text):
        if char == '"' and not escape: in_string = not in_string
        elif char == '\\' and not escape: escape = True
        else: escape = False

        if not in_string:
            if char == '{':
                if depth == 0: start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        obj = json.loads(text[start:i+1])
                        if isinstance(obj, dict) and "name" in obj:
                            jsons.append((obj, start, i+1))
                    except Exception: pass
    return jsons

@app.route('/api/chat', methods=['POST'])
@app.route('/v1/api/chat', methods=['POST'])
def proxy():
    start_time = time.time()
    try:
        ollama_data = request.json
        if not ollama_data: return json.dumps({"error": "No data received"}), 400
            
        original_messages = ollama_data.get('messages', [])
        requested_model = ollama_data.get('model', OPENCLAW_MODEL_ID)
        
        if DEBUG_MODE:
            print(f"\n\n[DEBUG] {'='*60}")
            print(f"[DEBUG] REQUEST RECEIVED: {datetime.now().strftime('%H:%M:%S')}")
        else:
            print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] --- NEW REQUEST ---")
            if original_messages and original_messages[-1]['role'] == 'user':
                print(f"{original_messages[-1]['content']}")
                print("-" * 40)

        # --- INPUT RESCUE (SHORT-CIRCUITING) ---
        # Scans incoming messages (e.g. from Cron jobs) for keywords to bypass the LLM entirely.
        # ONLY trigger if the LAST message is from the 'user'. 
        # If the last message is a 'tool' result, we must not short-circuit, or we create an infinite loop!
        if not PASS_THROUGH_MODE and ENABLE_INPUT_RESCUE and original_messages:
            last_msg = original_messages[-1]
            if last_msg.get('role') == 'user':
                last_user_msg = last_msg.get('content', '').lower()
                for rescue in EMERGENCY_RESCUES:
                    if all(kw.lower() in last_user_msg for kw in rescue["keywords"]):
                        if DEBUG_MODE:
                            print(f"[DEBUG] INPUT-RESCUE TRIGGERED: Short-circuiting message to {rescue['command']}")
                        
                        def short_circuit_stream():
                            msg_obj = {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [{"function": {"name": "exec", "arguments": {"command": rescue["command"]}}}]
                            }
                            yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": msg_obj, "done": False}).encode('utf-8') + b'\n'
                            yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": {"role": "assistant", "content": ""}, "done": True}).encode('utf-8') + b'\n'
                        return Response(short_circuit_stream(), content_type='application/x-ndjson')

        # Loop breaker for Gateway errors - Bypass if in pass-through mode
        if not PASS_THROUGH_MODE and original_messages and original_messages[-1].get('role') == 'tool':
            content_str = str(original_messages[-1].get('content', ''))
            if "Message failed" in content_str or "No active WhatsApp" in content_str:
                if DEBUG_MODE:
                    print("[DEBUG] Intercepted message tool failure. Stopping loop.")
                def short_circuit():
                    yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": {"role": "assistant", "content": ""}, "done": True}).encode('utf-8') + b'\n'
                return Response(short_circuit(), content_type='application/x-ndjson')

        # --- SMART AMNESIA LOGIC ---
        

        apply_amnesia = False
        if not PASS_THROUGH_MODE and ENABLE_SMART_AMNESIA:
            # Amnesia only when the current turn processes a tool result (last message is tool)
            if original_messages and original_messages[-1].get('role') == 'tool':
                apply_amnesia = True

        if apply_amnesia:
            messages = []
            if len(original_messages) > 0:
                messages.append(original_messages[0])
            if len(original_messages) > 1:
                last_msgs = []
                for msg in reversed(original_messages[1:]):
                    last_msgs.append(msg)
                    if msg['role'] == 'user':
                        break
                last_msgs.reverse()
                messages.extend(last_msgs)
            if DEBUG_MODE:
                print(f"[DEBUG] SMART AMNESIA APPLIED: Truncated history to {len(messages)} messages.")
        elif not PASS_THROUGH_MODE and ENABLE_SMART_AMNESIA and len(original_messages) > CHAT_HISTORY_LIMIT + 1:
            # Chat mode: System prompt + last CHAT_HISTORY_LIMIT messages
            messages = [original_messages[0]] + original_messages[-(CHAT_HISTORY_LIMIT):]
            if DEBUG_MODE:
                print(f"[DEBUG] SMART AMNESIA CHAT-TRIM: Kept last {CHAT_HISTORY_LIMIT} messages.")
        else:
            messages = original_messages
            if not PASS_THROUGH_MODE and DEBUG_MODE and len(original_messages) > 1:
                print(f"[DEBUG] SMART AMNESIA OFF: History within limit, preserving full.")

        # Clean up System Prompt & Configurable Trimming for 7B Cognitive Overload
        if not PASS_THROUGH_MODE and messages and messages[0]['role'] == 'system':
            sys_msg = messages[0]['content']
            sys_msg = re.sub(r'## Silent Replies.*?(?:Right: NO_REPLY|NO_REPLY)', '', sys_msg, flags=re.DOTALL)
            sys_msg = sys_msg.replace("When you have nothing to say, respond with ONLY: NO_REPLY", "")
            dynamic_pattern = r"(?i)(The current date and time is.*?$|Current Time:.*?$|Date:.*?$)"
            dynamic_parts = re.findall(dynamic_pattern, sys_msg, flags=re.MULTILINE)
            if dynamic_parts:
                sys_msg = re.sub(dynamic_pattern, "", sys_msg, flags=re.MULTILINE).strip()
                if len(messages) > 1 and messages[-1]['role'] == 'user':
                    messages[-1]['content'] += "\n\n[System-Info: " + " | ".join(dynamic_parts) + "]"
            
            if ENABLE_PROMPT_TRIMMING and TRIM_SKILLS:
                skills_pattern = "|".join(map(re.escape, TRIM_SKILLS))
                sys_msg = re.sub(fr'<skill>\s*<name>(?:{skills_pattern})</name>.*?</skill>', '', sys_msg, flags=re.DOTALL)
                sys_msg = re.sub(r'\n\s*\n', '\n', sys_msg) # Clean up empty lines
            
            messages[0]['content'] = sys_msg

        # Attention-Forcer Injection for small models
        if not PASS_THROUGH_MODE and ENABLE_ATTENTION_FORCER and messages and messages[-1].get('role') == 'user':
            if ATTENTION_FORCER_TEXT not in messages[-1]['content']:
                messages[-1]['content'] += ATTENTION_FORCER_TEXT

        # Format sanitization for OpenAI endpoint (Required for both modes to prevent 400 Bad Request)
        for msg in messages:
            if msg.get('role') == 'tool' and isinstance(msg.get('content'), dict):
                 msg['content'] = json.dumps(msg['content'])
            if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                 for tc in msg.get('tool_calls', []):
                     if isinstance(tc, dict) and 'function' in tc:
                         args = tc['function'].get('arguments')
                         if isinstance(args, dict):
                             tc['function']['arguments'] = json.dumps(args)

        openai_payload = {
            "model": LLM_MODEL_IDENTIFIER,
            "messages": messages,
            "stream": True,
            "temperature": ollama_data.get('options', {}).get('temperature', 0.0),
            "max_tokens": ollama_data.get('options', {}).get('num_predict', 4096)
        }

        if 'tools' in ollama_data:
            if not PASS_THROUGH_MODE:
                # Filter tools to remove web_search (forces web_fetch)
                filtered_tools = []
                for t in ollama_data['tools']:
                    if t.get('function', {}).get('name') == 'web_search':
                        continue
                    filtered_tools.append(t)
                
                tools = filtered_tools
                
                if not any(t.get('function', {}).get('name') == 'exec' for t in tools):
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": "exec",
                            "description": "Run shell commands natively on the system.",
                            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
                        }
                    })
                if not any(t.get('function', {}).get('name') == 'upload_audio' for t in tools):
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": "upload_audio",
                            "description": "Uploads an audio file as a voice message.",
                            "parameters": {"type": "object", "properties": {"media": {"type": "string"}}, "required": ["media"]}
                        }
                    })
                openai_payload['tools'] = tools
            else:
                # In PASS_THROUGH_MODE, simply forward the tools unmodified
                openai_payload['tools'] = ollama_data['tools']

        if DEBUG_MODE:
            print(f"[DEBUG] OUTGOING PAYLOAD TO LLM:")
            if PASS_THROUGH_MODE:
                print(f"[DEBUG] (PASS_THROUGH_MODE IS ACTIVE - Content sent unmodified)")
            print(json.dumps(openai_payload, indent=2, ensure_ascii=False))
            print(f"[DEBUG] {'-'*60}")

        req = requests.post(LLM_SERVER_URL, json=openai_payload, stream=True, timeout=600)
        if req.status_code != 200:
            return json.dumps({"error": f"LLM Server Error {req.status_code}", "details": req.text}), 502

        def generate():
            token_count = 0
            full_content = ""
            merged_tools = {}
            
            # --- Detect if this conversation originated from a Cron job ---
            is_cron_job = False
            for m in reversed(messages):
                if m.get('role') == 'user':
                    if '[cron:' in m.get('content', '').lower():
                        is_cron_job = True
                    break
            # Also detect systemEvent jobs (no [cron:] prefix, but inbound_meta channel=cron-event)
            if not is_cron_job and messages and messages[0].get('role') == 'system':
                if '"channel": "cron-event"' in messages[0].get('content', ''):
                    is_cron_job = True
            
            for line in req.iter_lines():
                if not line: continue
                line_text = line.decode('utf-8')
                if line_text.startswith("data: "):
                    data_str = line_text[6:].strip()
                    if data_str == "[DONE]": break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk['choices'][0]['delta']
                        if 'content' in delta and delta['content']:
                            full_content += delta['content']
                            token_count += 1
                        if 'tool_calls' in delta:
                            for tc in delta['tool_calls']:
                                idx = tc.get('index', 0)
                                if idx not in merged_tools: merged_tools[idx] = {"name": "", "arguments": ""}
                                if 'function' in tc:
                                    func = tc['function']
                                    if 'name' in func and func['name']: merged_tools[idx]["name"] += func['name']
                                    if 'arguments' in func and func['arguments']: merged_tools[idx]["arguments"] += func['arguments']
                    except Exception: continue
            
            duration = time.time() - start_time
            full_content = full_content.replace('<|im_end|>', '').replace('<|im_start|>', '').replace('<|endoftext|>', '').strip()
            
            final_tool_calls = []
            for idx, func in merged_tools.items():
                if func["name"]: final_tool_calls.append({"function": {"name": func["name"], "arguments": func["arguments"]}})
                        
            BT = chr(96)
            
            if not PASS_THROUGH_MODE:
                if not final_tool_calls and full_content:
                    extracted = extract_hallucinated_tools(full_content)
                    if extracted:
                        for obj, start, end in reversed(extracted):
                            args_str = obj["arguments"] if isinstance(obj["arguments"], str) else json.dumps(obj["arguments"])
                            final_tool_calls.append({"function": {"name": obj["name"], "arguments": args_str}})
                            full_content = full_content[:start] + full_content[end:]
                        
                        final_tool_calls.reverse()

                if not final_tool_calls and full_content:
                    rescued_command = None
                    match_start = -1
                    escaped_base = re.escape(EXPECTED_SCRIPT_BASE_PATH)
                    
                    bash_re = BT * 3 + r'(?:[a-zA-Z]*)?\s*\n(.*?' + escaped_base + r'.*?)\n' + BT * 3
                    bash_match = re.search(bash_re, full_content, re.DOTALL)
                    
                    if bash_match:
                        command = bash_match.group(1).strip()
                        if command:
                            final_tool_calls.append({"function": {"name": "exec", "arguments": json.dumps({"command": command})}})
                            full_content = full_content[:bash_match.start()].strip()
                            if DEBUG_MODE:
                                print(f"[DEBUG] BASH-RESCUE: Converted code block to exec call: {command}")
                    else:
                        naked_match = re.search(r'(?m)^((?:bash\s+)?' + escaped_base + r'[^\n]+)$', full_content)
                        if naked_match: 
                            command = naked_match.group(1).strip()
                            final_tool_calls.append({"function": {"name": "exec", "arguments": json.dumps({"command": command})}})
                            full_content = full_content[:naked_match.start()].strip()
                            if DEBUG_MODE:
                                print(f"[DEBUG] BASH-RESCUE: Converted naked string to exec call: {command}")
                        elif ENABLE_EMERGENCY_RESCUE:
                            content_lower = full_content.lower()
                            for rescue in EMERGENCY_RESCUES:
                                if all(kw.lower() in content_lower for kw in rescue["keywords"]):
                                    command = rescue["command"]
                                    
                                    # Anti-Loop Check: Was this exact command already executed in the current history?
                                    already_executed = False
                                    for m in original_messages:
                                        if m.get('role') == 'assistant' and 'tool_calls' in m:
                                            for tc in m.get('tool_calls', []):
                                                if isinstance(tc, dict) and tc.get('function', {}).get('name') == 'exec':
                                                    args = tc.get('function', {}).get('arguments', '')
                                                    if isinstance(args, str) and command in args:
                                                        already_executed = True
                                                    elif isinstance(args, dict) and command in args.get('command', ''):
                                                        already_executed = True
                                                        
                                    if already_executed:
                                        continue # Skip this rescue so the LLM can actually answer the user!
                                        
                                    final_tool_calls.append({"function": {"name": "exec", "arguments": json.dumps({"command": command})}})
                                    full_content = full_content[:0].strip()
                                    if DEBUG_MODE:
                                        print(f"[DEBUG] BASH-RESCUE: Triggered emergency rescue to exec call: {command}")
                                    break

                pattern_empty_block = BT * 3 + r'(?:json)?\s*' + BT * 3
                full_content = re.sub(pattern_empty_block, '', full_content)
                pattern_dangling = r'\s*' + BT * 3 + r'(?:json)?\s*$'
                full_content = re.sub(pattern_dangling, '', full_content)
                full_content = re.sub(r'</?tool_[a-z_]+>', '', full_content)
                full_content = re.sub(r'\n{3,}', '\n\n', full_content).strip()

                cleaned_text = full_content.replace(BT * 3 + 'json', '').replace(BT * 3, '').strip()
                if cleaned_text == "NO_REPLY" or '{"name":"NO_REPLY"}' in cleaned_text.replace(" ", "").replace("\n", ""):
                    full_content = "NO_REPLY"

                for tc in final_tool_calls:
                    if tc['function']['name'] == 'upload_audio':
                        tc['function']['name'] = 'message'
                        try:
                            args = json.loads(tc['function']['arguments'])
                            args['action'] = 'send'
                            args['channel'] = AUTO_DELIVERY_CHANNEL
                            if AUTO_DELIVERY_TARGET: args['target'] = AUTO_DELIVERY_TARGET
                            args['message'] = AUDIO_DELIVERY_MESSAGE
                            tc['function']['arguments'] = json.dumps(args)
                        except: pass

                # --- THE LOOP BREAKER ---
                # Only suppress Auto-Delivery if the CURRENT response already contains a 'message' call.
                # Checking full history caused false positives (e.g., the greeting call blocked all future deliveries).
                message_tool_used = any(
                    tc.get('function', {}).get('name') == 'message'
                    for tc in final_tool_calls
                )

                # --- UNIVERSAL AUTO-DELIVERY ---
                # Force delivery to WhatsApp if it's a cron job, because Cron lacks a native chat interface
                should_auto_deliver = FORCE_AUTO_DELIVERY or (FORCE_CRON_DELIVERY and is_cron_job)
                
                if should_auto_deliver and full_content and full_content != "NO_REPLY":
                    if not final_tool_calls and not message_tool_used:  
                        auto_args = {
                            "action": "send",
                            "channel": AUTO_DELIVERY_CHANNEL,
                            "message": full_content
                        }
                        if AUTO_DELIVERY_TARGET:
                            auto_args["target"] = AUTO_DELIVERY_TARGET

                        auto_push_tool = {
                            "function": {
                                "name": "message",
                                "arguments": json.dumps(auto_args)
                            }
                        }
                        final_tool_calls.append(auto_push_tool)
                        if DEBUG_MODE:
                            reason = "Cron Job" if is_cron_job and not FORCE_AUTO_DELIVERY else "Global Config"
                            print(f"[SYSTEM] Auto-Delivery active ({reason})! Text routed to {AUTO_DELIVERY_CHANNEL}.")
                        full_content = "NO_REPLY"
                    elif not final_tool_calls and message_tool_used:
                        if DEBUG_MODE:
                            print("[SYSTEM] Loop-Breaker active: 'message' tool was already used. Suppressing Auto-Delivery.")

            print(f"[DEBUG] Finished in {duration:.2f}s | Tokens: {token_count}")
            
            if final_tool_calls:
                for tc in final_tool_calls:
                    print(f"[TOOL_CALL DETECTED: {tc['function']['name']}]")
                    try: print(json.dumps(json.loads(tc['function']['arguments']), indent=2))
                    except: print(tc['function']['arguments'])
                    
            if full_content and full_content != "NO_REPLY": 
                print(f"[TEXT]: {full_content}")
            elif not final_tool_calls: 
                print(f"[WARNING]: Model is Silent (NO_REPLY).")

            message_obj = {"role": "assistant", "content": full_content if full_content != "NO_REPLY" else ""}
            if final_tool_calls:
                output_tool_calls = []
                for tc in final_tool_calls:
                    args = tc['function']['arguments']
                    try: args = json.loads(args) if isinstance(args, str) else args
                    except: args = {}
                    output_tool_calls.append({"function": {"name": tc['function']['name'], "arguments": args}})
                message_obj["tool_calls"] = output_tool_calls

            yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": message_obj, "done": False}).encode('utf-8') + b'\n'
            yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": {"role": "assistant", "content": ""}, "done": True}).encode('utf-8') + b'\n'

        return Response(generate(), content_type='application/x-ndjson')
    except Exception as e:
        print(f"\n[ERROR] Proxy error: {e}")
        return json.dumps({"error": str(e)}), 502

if __name__ == '__main__':
    if '-stop' in sys.argv:
        kill_other_instances()
        sys.exit(0)
        
    if '-restart' in sys.argv:
        kill_other_instances()

    print(f"==========================================")
    print(f"ClawCut Universal Proxy (V3.1.9)")
    print(f"PROFILE SELECTED: {SELECTED_PROFILE.upper()} ({cfg['ip']}:{cfg['port']})")
    print(f"MODEL USED: {cfg['model_name']}")
    print(f"PASS_THROUGH_MODE = {PASS_THROUGH_MODE}")
    if not PASS_THROUGH_MODE:
        print(f"SMART_AMNESIA = {ENABLE_SMART_AMNESIA}")
        print(f"AUTO_DELIVERY = {FORCE_AUTO_DELIVERY} (Cron: {FORCE_CRON_DELIVERY}) -> {AUTO_DELIVERY_CHANNEL}:{AUTO_DELIVERY_TARGET}")
    if WRITE_TO_LOGFILE: print(f"LOGGING TO: {PATH_TO_LOGFILE} (Max Size: {DELETE_LOG_SIZE})")
    print(f"==========================================")
    app.run(host='0.0.0.0', port=5000, debug=DEBUG_MODE, use_reloader=False)
