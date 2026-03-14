# **ClawCut Proxy**

OpenClaw is a powerful framework that, by default, sends massive system prompts 
(often >28,000 characters) and complex tool definitions (JSON tools) to the LLM. 
While large cloud models or high-end local models (14B etc.) handle this well, 
small models (7B, 8B) running on limited hardware (Mac/MLX or Raspberry Pi) 
often suffer from "Cognitive Overload". This is where ClawCut steps in.

ClawCut is an experimental proxy to manipulate, inject JSON-Calls and 
extract JSON clutter from OpenClaw. 

BENEFITS OF USING CLAWCUT:
 
- Extreme processing latency (slow Time To First Token).
- Forgetting their identity or available tools.
- Hallucinating text answers instead of executing local scripts.
- Connection timeouts or malformed JSON responses.
- Huge RAM consumption

This proxy acts as a "Man-in-the-Middle" between OpenClaw and your local LLM 
server to optimize the data flow:

-  PROMPT TRIMMING: Automatically removes unused default skills from the system 
   prompt to keep the context window small and focused.
-  SMART AMNESIA: Intelligently truncates chat history after successful tool 
   executions to free up "mental space" for the model.
-  ATTENTION FORCER: Injects a reminder at the very end of the user query to 
   ensure the model prioritizes tool usage.
-  TOOL FORCER: Injects keywords for tool calling and points to commands.
-  INPUT RESCUE: Short-circuits known incoming requests (like Cron-Jobs) to 
   bypass LLM latency and ensure 100% reliability for automated tasks.
-  BASH-RESCUE: Detects poorly formatted script calls (e.g., naked code blocks) 
   and converts them into valid OpenClaw tool calls on the fly.
-  Automatically filters dynamic timestamps from system prompts to enable near-instant 
   responses via hardware caching.
-  Translates between OpenAI-compatible streams (MLX) and the Ollama/NDJSON 
   format expected by OpenClaw.
-  Real-time console output of prefill duration, token count, and generation 
   speed (tokens per second).
-  With the **DEBUG\_MODE** enabled, you can inspect the full "JSON Clutter" 
   sent by OpenClaw to understand exactly what the model is processing.

PERFORMANCE:
- Significantly faster response times (TTFT), as the model has much less text 
  to process upfront.
- Improved reliability when using and calling scripts (bash or whatever).
- Robust error handling for stream interruptions or formatting errors.

WHEN TO USE:
- Ideal for small models (7B-8B) running on hardware like Mac (MLX), Windows 
  or Linux.
- If your model "chats" too much instead of executing commands.

WHEN TO USE WITH CAUTION:
- If you are using highly intelligent, large models (14B+) that can handle 
  complex prompts natively. In this case, the proxy can act purely as a logger 
  and format translator without manipulating the content if PASS_THROUGH_MODE = True.

<img width="1021" height="975" alt="Image" src="https://github.com/user-attachments/assets/9810a45d-6697-47a7-9597-c22a59203b4c" />

**How to find & download MLX Models**

You don't need to manually download model files. The mlx-lm server handles everything automatically.

1. **Browse Models:** Go to [Hugging Face](https://huggingface.co/mlx-community) and search for the `mlx-community organization`. They provide pre-converted models optimized for Apple Silicon.

2. **Choose your Model:** Copy the repository name (e.g., mlx-community/Qwen2.5-14B-Instruct-4bit).

3. **Automatic Download:** When you start the server for the first time using the --model flag, mlx-lm will automatically download the files (several GBs) and cache them locally on your Mac.

## **Prerequisites**

* **Python 3.x**  
* **Network Access:** Both devices must be in the same local network.  
* **MLX-LM Server (on Mac):** The Mac must be configured to listen to network requests.

## **Configuration & Network Setup**

### **1\. Prepare the Mac (The LLM Server)**

To allow the Raspberry Pi to talk to your Mac, the MLX server must not only run on localhost. You **must** bind it to your network interface.  
Start the server on your Mac with the `--host 0.0.0.0` flag: 

```bash
python -m mlx_lm.server --model [YOUR_MODEL_ID] --host 0.0.0.0 --port 8080
```
*Note: Using 0.0.0.0 makes the LLM accessible to any device in your local network.*

⚠️ **IMPORTANT:** Replace `[YOUR_MODEL_ID]` with the model of your choice (e.g., `mlx-community/Qwen2.5-14B-Instruct-4bit`). Ensure that the model fits your available RAM (a 14B model requires approx. 9-10 GB RAM, a 32B model approx. 19 GB). Choose a smaller model (e.g., 7B) if your Mac only has 8 GB or 16 GB of RAM.  

⚠️ **Note on Performance:** The very first request (or the first one after clearing a chat session) will take significantly longer (often 30-60 seconds) because the Mac has to process the entire 16k context for the first time. **ClawCut-MLX** optimization becomes effective starting with the **second** request, reducing response times to just a few seconds.  

#### **⚠️ macOS Firewall Note**

If the connection is still refused (Error 502/61), your macOS firewall might be blocking the port.

* Go to **`System Settings > Network > Firewall`**.  
* Either disable it temporarily for testing or click **Options** and ensure that your Python binary (inside your `mlx_env`) is allowed to receive incoming connections.  
* **Test connection from Pi:** Run `nc -zv [MAC_IP] 8080\`. It should say "succeeded".

### **2\. Configure the Proxy (on Raspberry Pi)**

Edit the clawcut-mlx.py file and adjust the constants:

* `MAC_IP:` The local IP address of your Mac (e.g., `192.168.0.5`).  
* `OPENCLAW_MODEL_ID:` The exact model ID used in your openclaw.json.  
* `MLX_MODEL_IDENTIFIER:` The name of the model loaded on your Mac.  
* `DEBUG_MODE:` Set to True to see the raw communication and JSON clutter.

## **Installation**

### **1\. Clone the repository**

```bash
git clone [https://github.com/back-me-up-scotty/ClawCut.git](https://github.com/back-me-up-scotty/ClawCut.git)  
cd clawcut-mlx
```

### **2\. Create a Virtual Environment (on MAC / Recommended)**

```bash
python3 -m venv proxy env  
source proxy_env/bin/activate
```

### **3\. Install Dependencie (on MAC)s**

```bash
pip install flask requests
```

### **3\. Install Dependencies (on Pi)**
```bash
chmod +x /home/user/clawcut-mlx/clawcut-mlx.py
```

## **Usage**

Start the proxy on your Raspberry Pi:  

```bash
python3 clawcut-mlx.py
```

### **OpenClaw Configuration (openclaw.json)**

Point your OpenClaw provider to the proxy. If OpenClaw and the Proxy are on the same Pi, use the following configuration:  

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
            "id": "ollama/qwen2.5:14b",  
            "name": "qwen 2.5 14b",  
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
        "primary": "ollama/qwen2.5:14b"  
      }  
    }  
  }
```

