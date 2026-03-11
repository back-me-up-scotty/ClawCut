# **ClawCut MLX Proxy**

A high-performance bridge between **OpenClaw** and **Apple Silicon (MLX-LM)**.  
This proxy allows you to run large language models (like Qwen 2.5) on a Mac mini/MacBook (M1/M2/M3/M4) while maintaining compatibility with the OpenClaw framework running on a Raspberry Pi or other Linux servers.

## **Motivation**

OpenClaw is a powerful framework, but it often sends a massive amount of "JSON Clutter" (system prompts, tool definitions, and metadata) in every request. This often leads to:

* **LLM Timeouts:** Standard setups frequently run into timeouts because the model takes too long to process the massive context.  
* **Poor Reasoning:** Models can get "lost" in the clutter, leading to hallucinations or ignored tool calls.

**ClawCut-MLX** solves this by optimizing the communication and leveraging the power of Apple Silicon. While this setup is optimized for speed, the performance depends on your hardware:

* **Example:** With a **Mac mini M4 Pro (24 GB RAM)** and a **14B model**, this setup achieves generation speeds of up to **21+ tokens/s** with a warm KV-cache.  
* **Flexibility:** The proxy works with any MLX-compatible model. You can use smaller models (e.g., 7B) for even higher speeds or larger models (e.g., 32B+) if your Mac has sufficient Unified Memory.

## **Typical Use Case (Split Setup)**

This proxy is specifically designed for users who run a **split-system architecture**:

1. **The Brain (Mac):** A powerful Mac mini or MacBook acts as the LLM engine, providing high-speed inference.  
2. **The Heart (Raspberry Pi/Linux):** A Pi or Linux server hosts the OpenClaw framework, managing integrations like WhatsApp, Telegram, or home automation.

By offloading the heavy lifting to the Mac, the Raspberry Pi remains responsive, and the LLM responses become near-instant.

## **Key Features**

* **KV-Cache Optimization:** Automatically filters dynamic timestamps from system prompts to enable near-instant responses via hardware caching.  
* **Protocol Translation:** Translates between OpenAI-compatible streams (MLX) and the Ollama/NDJSON format expected by OpenClaw.  
* **Performance Tracking:** Real-time console output of prefill duration, token count, and generation speed (tokens per second).  
* **Transparency:** With the **DEBUG\_MODE** enabled, you can inspect the full "JSON Clutter" sent by OpenClaw to understand exactly what the model is processing.

## **How to find & download MLX Models**

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
python -m mlx_lm.server --model [YOUR\_MODEL\_ID\] --host 0.0.0.0 --port 8080
```

⚠️ **IMPORTANT:** Replace `[YOUR_MODEL_ID\]` with the model of your choice (e.g., `mlx-community/Qwen2.5-14B-Instruct-4bit`). Ensure that the model fits your available RAM (a 14B model requires approx. 9-10 GB RAM, a 32B model approx. 19 GB). Choose a smaller model (e.g., 7B) if your Mac only has 8 GB or 16 GB of RAM.  

⚠️ **Note on Performance:** The very first request (or the first one after clearing a chat session) will take significantly longer (often 30-60 seconds) because the Mac has to process the entire 16k context for the first time. **ClawCut-MLX** optimization becomes effective starting with the **second** request, reducing response times to just a few seconds.  
*Note: Using 0.0.0.0 makes the LLM accessible to any device in your local network.*

#### **⚠️ macOS Firewall Note**

If the connection is still refused (Error 502/61), your macOS firewall might be blocking the port.

* Go to **`System Settings > Network > Firewall`**.  
* Either disable it temporarily for testing or click **Options** and ensure that your Python binary (inside your `mlx\_env`) is allowed to receive incoming connections.  
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

### **MIT License**

Copyright (c) 2026 Niels Gerhardt  

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal  
in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell  
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:  
The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. 

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,  
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER  
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE  
SOFTWARE.
