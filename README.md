# ClawCut Universal LLM Bridge & Proxy 4.0.4

ClawCut is a proxy that sits between OpenClaw and any LLM — local or cloud. It solves the "Cognitive Overload" problem for small models, translates between API formats, and lets you switch between completely different backends (local Ollama, local MLX, NVIDIA cloud, OpenAI, etc.) by simply restarting with a different profile flag. Your `openclaw.json` never needs to change.

<img width="1203" height="1217" alt="Image" src="https://github.com/user-attachments/assets/3181bb94-6cb7-4923-86ce-d34067ee170a" />

---

## HOW IT WORKS

OpenClaw always sends to `http://127.0.0.1:5000/v1`. ClawCut intercepts, manipulates or forwards the request based on the active profile, and returns the response in the format OpenClaw expects.

```
OpenClaw → ClawCut Proxy → Local LLM (MLX / Ollama)
                         → Cloud API  (NVIDIA / OpenAI / etc.)
```

The specific model name in `openclaw.json` is irrelevant — ClawCut always overrides it with the active profile's model. However, the provider prefix in the `id` field (e.g. `ollama/`) is **not** irrelevant: OpenClaw uses it to resolve the provider before routing the request. Without the correct prefix, OpenClaw fails before the proxy is ever involved.

---

## NOTE PRIOR TO INSTALLATION

There is no guarantee that ClawCut will work in every 
configuration or with future OpenClaw updates. 

---

## WHEN TO USE
- Ideal for small models (7B-8B) running on hardware like Mac (MLX), Windows 
  or Linux.
- If your model "chats" too much instead of executing commands.

---

## WHEN TO USE WITH CAUTION
- If you are using highly intelligent, large models (14B+) that can handle 
  complex prompts natively. In this case, the proxy can act purely as a logger 
  and format translator without manipulating the content.
---

## PROBLEMS CLAWCUT SOLVES

- Extreme processing latency (slow Time To First Token) on small models
- Models forgetting their identity or available tools
- Models hallucinating text instead of executing scripts
- Connection timeouts or malformed JSON responses
- Huge RAM consumption from massive system prompts
- Format incompatibility between OpenAI-compatible APIs and Ollama/NDJSON

---

## FEATURES

- **WEB-DASHBOARD** - OpenClaw can be configured either through a web dashboard or directly in the file itself.
- **PROFILE SWITCHING** — Switch between any number of local or cloud LLM backends using a CLI flag. No changes to `openclaw.json` required.
- **PASS-THROUGH MODES** — Three levels: full proxy intervention, small (format-only), or full cloud passthrough.
- **CLOUD PROVIDER SUPPORT** — Connect to NVIDIA, OpenAI, or any OpenAI-compatible API via profile configuration.
- **PROMPT TRIMMING** — Strips unused skills from the system prompt to keep context small.
- **SMART AMNESIA** — Truncates chat history after tool executions to free context for the model.
- **ATTENTION FORCER** — Injects a reminder at the end of user messages to enforce tool usage.
- **INPUT RESCUE** — Short-circuits known incoming requests (Cron jobs) to bypass LLM latency.
- **BASH RESCUE** — Converts poorly formatted script calls into valid OpenClaw tool calls on the fly.
- **STREAM TRANSLATION** — Translates OpenAI SSE streams (cloud/MLX) to Ollama NDJSON format.
- **DEBUG MODE** — Full JSON payload logging to console and logfile.

---

## SETUP

### Hardware Reference

To give you an idea of the setup I use to run my LLM locally in combination with OpenClaw and ClawCut:

| Role | Example |
|------|---------|
| LLM1 (local, fast) | MacMini M4 Pro 24 GB · mlx-community/Qwen2.5-Coder-7B-Instruct-4bit |
| LLM2 (local, large) | Windows · RTX 3060 12 GB VRAM· Ollama qwen2.5:14b |
| LLM3 (cloud) | NVIDIA NIM API · moonshotai/kimi-k2.5 |
| Proxy host + OpenClaw | Raspberry Pi 5 · 16 GB RAM |

### Prerequisites

To execute ClawCut and run the Dashboard you have to install Python 3, Flask and requests:

```bash
# Linux / Raspberry Pi
sudo apt update && sudo apt install python3-pip python3-venv -y
cd /home/user/ClawCut/
python3 -m venv proxy_env
source proxy_env/bin/activate
pip install Flask requests

# macOS
python3 -m venv proxy_env && source proxy_env/bin/activate && pip install Flask requests

# Windows (PowerShell)
python -m venv proxy_env && .\proxy_env\Scripts\Activate.ps1 && pip install Flask requests
```

---

## OPENCLAW CONFIGURATION

Point OpenClaw to the proxy. The `openclaw.json` stays exactly like this regardless of which profile you start ClawCut with:

```json
"models": {
    "mode": "merge",
    "providers": {
      "ollama": {
        "baseUrl": "http://127.0.0.1:5000/v1",
        "apiKey": "ollama-local",
        "api": "ollama",
        "models": [
          {
            "id": "ollama/ClawCut",
            "name": "ClawCut",
            "reasoning": false,
            "input": [
              "text"
            ],
            "cost": {
              "input": 0,
              "output": 0,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 16384,
            "maxTokens": 4096,
            "compat": {
              "supportsDeveloperRole": false
            }
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "ollama/ClawCut"
      },
```

The only value that matters here is `"baseUrl": "http://127.0.0.1:5000/v1"` or `"baseUrl": "http://192.xxx.xxx.xxx:5000/v1"` if ClawCut is on a different host. Everything else — model name, API key, context window — is ignored and overridden by the active ClawCut profile.

⚠️ **The `ollama/` prefix in the `id` field is not optional.** OpenClaw uses it to resolve the provider before routing the request. Without it, OpenClaw misidentifies the provider (⚠️ Agent failed before reply: Unknown model: anthropic/ClawCut.
Logs: openclaw logs --follow) and fails before the proxy is ever involved — regardless of what `baseUrl` is set to.

---

## PROFILE CONFIGURATION

You can edit and configure ClawCut either directly via the `clawcut.py` file or simply through the web dashboard. To do this, start ClawCut 
from the console (See -> "STARTING CLAWCUT") and then point your browser to `http://127.0.0.1:5000` or, if ClawCut is running on a different host, 
`http://IP-Adresse:5000`. Profiles support both local servers and cloud APIs. 

```python
PROFILES = {

    # Local MLX (Mac) — full proxy intervention
    "LLM1": {
        "ip": "192.168.0.xxx",
        "port": 8090,
        "model_id": "ollama/Qwen2.5-Coder-7B-Instruct-4bit",
        "model_name": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        "pass_through": False
    },

    # Local Ollama — format translation only, no content manipulation
    "LLM2": {
        "ip": "192.168.0.xxx",
        "port": 11434,
        "model_id": "ollama/qwen2.5:14b",
        "model_name": "qwen2.5:14b",
        "pass_through": "small"
    },

    # Cloud API (NVIDIA / OpenAI / etc.) — transparent forward
    "LLM3": {
        "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "api_key": "nvapi-yourkey",
        "model_id": "moonshotai/kimi-k2.5",
        "model_name": "moonshotai/kimi-k2.5",
        "headers": {},
        "pass_through": "full"
    }
}
```

### Profile Fields

| Field | Required for | Description |
|-------|--------------|-------------|
| `ip` + `port` | Local servers | ClawCut builds the URL as `http://ip:port/v1/chat/completions` |
| `base_url` | Cloud providers | Full endpoint URL, used as-is |
| `api_key` | Cloud providers | Added as `Authorization: Bearer` header |
| `headers` | Optional | Extra HTTP headers merged into the request |
| `model_id` | All | Model identifier sent to the LLM |
| `model_name` | All | Display name (informational) |
| `pass_through` | All | Proxy intervention level (see below) |

---

## PASS-THROUGH MODES

ClawCut supports four `pass_through` values. Despite the shared naming, these modes do **not** all behave the same way. Some are true pass-through styles, while others actively manipulate requests and responses. 

Each model behaves differently within the same mode. It’s best to test your way down from “Full” or in the reverse order and see which behavior leads to the result you want—that is, achieving a good balance between response time and the quality of the answers. 

For example, while I was able to get Qwen2.5-Coder-7B-Instruct-4bit to run tools just fine using the False setting and tool injection, Qwen3.5:9b did not respond at all when set to False. Conversely, Qwen2.5-Coder-7B-Instruct-4bit can no longer run tools when set to Small, Compact, or Full.

| `pass_through` value | Behavior | What ClawCut does | Best used for |
|---|---|---|---|
| `false` | Full proxy intervention | Enables prompt trimming, smart amnesia, attention forcer, input rescue, emergency rescue, loop breaking, tool filtering/augmentation, and output cleanup | Small local models that need strong guidance and stabilization |
| `"small"` | Format translation only | Keeps the translator pipeline between OpenClaw/Ollama-style input and OpenAI-style upstream requests, but skips the major intervention features | Stronger local models where you still want format bridging without heavy proxy behavior |
| `"compat"` | Compatibility pass-through | Keeps the pass-through architecture, but sanitizes message history, tool protocol, and tool schemas for stricter cloud endpoints | Cloud providers that are nominally OpenAI-compatible but fail on tool history, schemas, or specific fields |
| `"full"` | Raw pass-through | Forwards the request upstream with only minimal top-level cleanup and model override; no history cleanup, no tool sanitization, no rescue logic | Providers that already accept the payload as-is and where maximum transparency is desired |

### Detailed Behavior

#### `false`
This is the most interventionist mode. ClawCut actively modifies both the request and the response to improve reliability.

Enabled features include:
- `SMART_AMNESIA`
- prompt trimming
- attention forcing
- input rescue
- emergency rescue
- loop breaker logic
- tool filtering and tool augmentation
- output cleanup and tool-call reconstruction

Use this mode when small models tend to get confused, forget tools, respond in prose instead of calling commands, or become unstable under large prompts.

#### `"small"`
This mode keeps the translation layer but disables the main proxy manipulation features.

In practice, it:
- translates between OpenClaw/Ollama-style payloads and OpenAI-style upstream requests
- keeps stream reconstruction and tool-call reconstruction
- does **not** apply prompt trimming, smart amnesia, attention forcing, input rescue, or emergency rescue

Use this when you want a cleaner bridge layer without the stronger intervention logic.

#### `"compat"`
This mode exists for fragile cloud endpoints.

It keeps the pass-through architecture, but adds a narrow compatibility layer that can:
- remove prior `tool` messages from history
- remove historical `assistant.tool_calls`
- drop `tools` after prior tool protocol has appeared
- sanitize tool schemas for stricter providers

Use this when `"full"` fails because a provider claims OpenAI compatibility but crashes on real-world tool payloads.

#### `"full"`
This is the most transparent mode.

It:
- forwards the incoming request upstream
- overrides the model to the currently selected profile
- removes only a few top-level fields such as `options`, `tool_choice`, and `parallel_tool_calls`
- does **not** clean history
- does **not** sanitize tools
- does **not** inject or rescue anything

Use this when you want the proxy to stay as close to transparent as possible and the upstream provider is known to tolerate the payload.

### Recommendation

- Use `false` for weak or unstable local models.
- Use `"small"` for stronger local models that still need format translation.
- Use `"compat"` for strict cloud APIs that break in `"full"`.
- Use `"full"` when you want maximum transparency and the provider already works with the raw payload.

---

## STARTING CLAWCUT

```bash
# Start with default profile (LLM1)
/home/user/proxy_env/bin/python /home/user/ClawCut/clawcut.py

# Start with specific profile
/home/user/proxy_env/bin/python /home/user/ClawCut/clawcut.py -LLM2
/home/user/proxy_env/bin/python /home/user/ClawCut/clawcut.py -LLM3

# Kill old process and restart
/home/user/proxy_env/bin/python /home/user/ClawCut/clawcut.py -LLM2 -restart

# Flags can be combined in any order
/home/user/proxy_env/bin/python /home/user/ClawCut/clawcut.py -restart -LLM3
```

The `-restart` flag kills any running ClawCut process before starting the new one. Profile flags (`-LLM1`, `-LLM2`, `-LLM3`, etc.) are dynamic — any profile name defined in `PROFILES` works.

---

## CALLING THE DASHBOARD

As soon as ClawCut is running, point your browser to `http://127.0.0.1:5000` or, if ClawCut is running on a different host,use
`http://(IP-Address):5000`. 

⚠️ After the first launch, ClawCut automatically creates the `config.json` file. It contains all the settings specified in the script itself. `config.json` is the sole source of truth after the first launch. The hardcoded values in the script are merely fallback defaults for the very first launch, when no `config.json` file yet exists. So, before running ClawCut for the first time, you should set the variables directly in the script to values that work for your setup. The next time it runs, the script will load the config.json file and use it to override the hard-coded defaults in memory. The .py file itself will never be modified again. 

---

## FEATURE CONFIGURATION

### Logging

```python
DEBUG_MODE = True             # Print full JSON payloads to console
WRITE_TO_LOGFILE = True       # Also write to logfile
PATH_TO_LOGFILE = '/home/user/clawcut.log'
DELETE_LOG_SIZE = '10 MB'     # Rotate log at this size
```

### Smart Amnesia

Over time, chat histories grow too large for small models. Smart Amnesia watches the current turn: when the last message is a tool result (the model just received exec output), the proxy truncates all prior history. In normal chat mode, history is trimmed to `CHAT_HISTORY_LIMIT` messages.

```python
ENABLE_SMART_AMNESIA = True
CHAT_HISTORY_LIMIT = 10   # Messages kept in chat mode (excluding system)
```

### Prompt Trimming

Strips unused default skills from the system prompt before sending to the model.

```python
ENABLE_PROMPT_TRIMMING = True
TRIM_SKILLS = [
    "clawhub", "gemini", "gh-issues", "github", "healthcheck",
    "nano-pdf", "openai-whisper", "skill-creator", "summarize",
    "video-frames", "wacli", "weather"
]
```

### Attention Forcer

Injects a reminder at the end of every user message to enforce tool usage.

```python
ENABLE_ATTENTION_FORCER = True
ATTENTION_FORCER_TEXT = "\n\n[SYSTEM-REMINDER: NEVER respond to requests for local scripts, data, or services directly with text! You MUST use the 'exec' tool FIRST!]"
```

### Emergency & Input Rescue

`ENABLE_INPUT_RESCUE` — scans the incoming user message and short-circuits to an exec call without consulting the LLM at all. Useful for Cron jobs.

`ENABLE_EMERGENCY_RESCUE` — scans the LLM's text response and converts recognized keywords into exec calls if the model forgot to use the tool.

Scripts down below are examples how to use. These are my own scripts I want OpenClaw to call. Change to your scripts (if you have some) and set `ENABLE_EMERGENCY_RESCUE = True`

```python
ENABLE_EMERGENCY_RESCUE = True
ENABLE_INPUT_RESCUE = False
EXPECTED_SCRIPT_BASE_PATH = "/home/user/"

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
```

### Legacy: Auto-Delivery

```python
FORCE_AUTO_DELIVERY = False    # Legacy. Not needed for OpenClaw 3.12+.
FORCE_CRON_DELIVERY = False    # Known broken due to OpenClaw architecture. Legacy support only.
AUTO_DELIVERY_CHANNEL = "whatsapp"
AUTO_DELIVERY_TARGET = "+49123456"
```

---

## CLOUD PROVIDER SETUP

For cloud profiles, `openclaw.json` stays unchanged. All connection details live in the ClawCut profile:

**NVIDIA NIM example:**
```python
"LLM3": {
    "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
    "api_key": "nvapi-your-actual-key-here",
    "model_id": "moonshotai/kimi-k2.5",
    "model_name": "moonshotai/kimi-k2.5",
    "headers": {},
    "pass_through": "full"
}
```

In `"full"` passthrough mode, ClawCut automatically:
- Overrides the `model` field with the profile's `model_id`
- Adds `Authorization: Bearer <api_key>` to request headers
- Removes Ollama-specific fields (`options`, `tool_choice`)
- Filters `role: "tool"` messages from history (unsupported by cloud APIs)
- Filters empty assistant messages
- Translates the OpenAI SSE stream back to Ollama NDJSON for OpenClaw

---

## LOCAL MLX SERVER (MAC)

MLX is Apple's machine learning framework optimized for Apple Silicon (M1/M2/M3/M4). It lets you run quantized LLMs locally at high speed without needing a discrete GPU.

### Finding & Downloading Models

You don't need to manually download model files. The `mlx_lm` server handles everything automatically.

1. **Browse Models:** Go to [Hugging Face](https://huggingface.co/mlx-community) and search for the `mlx-community` organization. They provide pre-converted models optimized for Apple Silicon. Some models need an account/login to be able to download.

2. **Choose your Model:** Copy the repository name (e.g., `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit`).

3. **Automatic Download:** When you start the server for the first time using the `--model` flag, `mlx_lm` will automatically download the files (several GBs) and cache them locally on your Mac.

### Hugging Face Login (for gated models)

Some models (e.g., Llama, Mistral, Gemma) require you to accept a license agreement on Hugging Face before downloading. If you try to download a gated model without logging in, you'll get a 401 or access denied error.

**Step 1 — Create a free account**

Go to [https://huggingface.co](https://huggingface.co) and sign up.

**Step 2 — Accept the model license**

Open the model page on Hugging Face and click the "Agree and access repository" button if one is shown. Without this step, the download will fail even with a valid token.

**Step 3 — Create an Access Token**

Go to [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) and create a new token. A **Read** token is sufficient for downloading models.

**Step 4 — Log in on your Mac**

Install the Hugging Face CLI and log in:

```bash
pip install huggingface_hub
huggingface-cli login
```

Paste your token when prompted. It will be stored locally and used automatically by `mlx_lm` for all future downloads.

**Step 5 — Verify**

```bash
huggingface-cli whoami
```

This should print your username. You can now start the MLX server as normal — gated models will download without issues.

**Model size guide (choose based on your RAM):**

| RAM | Recommended size | Example |
|-----|-----------------|---------|
| 8 GB | 4B–7B (4-bit) | Qwen2.5-Coder-7B-Instruct-4bit |
| 16 GB | 7B–14B (4-bit) | Qwen2.5-14B-Instruct-4bit |
| 24 GB+ | 14B–32B (4-bit) | Qwen2.5-32B-Instruct-4bit |

### Installing mlx_lm

```bash
pip install mlx-lm
```

---

### Starting the Server

If ClawCut and your Mac are on the **same machine**:
```bash
python -m mlx_lm.server --model mlx-community/Qwen2.5-Coder-7B-Instruct-4bit --port 8090
```

If ClawCut runs on a **different machine** (e.g., a Raspberry Pi), you must bind to the network interface so the Pi can reach the Mac. Use `--host 0.0.0.0`:
```bash
python -m mlx_lm.server --model mlx-community/Qwen2.5-Coder-7B-Instruct-4bit --host 0.0.0.0 --port 8090
```

⚠️ `--host 0.0.0.0` makes the LLM accessible to any device on your local network. Only use this on a trusted home or office network.

⚠️ Replace the model name with the one you actually want to use. Make sure it fits your available RAM (see table above).

---

### macOS Firewall

If the connection is refused (Error 502), your macOS firewall may be blocking the port.

- Go to **System Settings → Network → Firewall**
- Either disable it temporarily for testing, or click **Options** and ensure your Python binary (inside your `mlx_env` or `venv`) is allowed to receive incoming connections
- Test the connection from the Pi: `nc -zv [MAC_IP] 8090` — it should say "succeeded"

---

### Profile Configuration for MLX

```python
"LLM1": {
    "ip": "192.168.0.xxx",   # Your Mac's local IP
    "port": 8090,
    "model_id": "ollama/Qwen2.5-Coder-7B-Instruct-4bit",
    "model_name": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
    "pass_through": False     # Full intervention — recommended for 7B models
}
```

Use `"pass_through": False` for 7B/8B models (they need trimming, amnesia, and tool injection to work reliably). Use `"pass_through": "small"` for 14B+ if you want less intervention.

---

### Performance Notes

- **First request is always slow** — the full 16k context window is processed for the first time. This can take 30–60 seconds on a 7B model.
- **From the second request onward**, ClawCut's caching and trimming kick in and response times drop to a few seconds.
- The `ENABLE_PROMPT_TRIMMING` and `ENABLE_SMART_AMNESIA` options have the most impact on MLX performance — keep both enabled for small models.

---

## NOTES

- The first request after a `/reset` or session start is always slower — the full context window is processed for the first time. From the second request onward, response times drop significantly.
- ClawCut is experimental. OpenClaw updates may break compatibility. Fork it, adapt it, share your results.

