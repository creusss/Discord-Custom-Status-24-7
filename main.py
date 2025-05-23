import os
import sys
import json
import time
import requests
import websocket
import threading
from keep_alive import keep_alive

status = os.getenv("status")  # online/dnd/idle
custom_status = os.getenv("custom_status")  # Custom status text
usertoken = os.getenv("token")

if not usertoken:
    print("[ERROR] Please add a token inside Secrets.")
    sys.exit()

headers = {"Authorization": usertoken, "Content-Type": "application/json"}

# Validate token
validate = requests.get("https://discord.com/api/v9/users/@me", headers=headers)
if validate.status_code != 200:
    print("[ERROR] Your token might be invalid. Please check it again.")
    sys.exit()

userinfo = requests.get("https://discord.com/api/v9/users/@me", headers=headers).json()
username = userinfo["username"]
discriminator = userinfo.get("discriminator", "0000")
userid = userinfo["id"]

class DiscordStatusBot:
    def __init__(self, token, status):
        self.token = token
        self.status = status
        self.ws = None
        self.heartbeat_interval = None
        self.heartbeat_thread = None
        self.should_heartbeat = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
    def connect(self):
        try:
            self.ws = websocket.WebSocket()
            # Add timeout and better connection options
            self.ws.settimeout(30)
            self.ws.connect("wss://gateway.discord.gg/?v=9&encoding=json", 
                          header={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            
            # Receive hello event
            hello = json.loads(self.ws.recv())
            self.heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
            
            # Start heartbeat in separate thread
            self.should_heartbeat = True
            self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop)
            self.heartbeat_thread.daemon = True
            self.heartbeat_thread.start()
            
            # Send identification
            self.identify()
            
            # Send custom status
            self.update_status()
            
            # Listen for events
            self.listen()
            
        except websocket.WebSocketException as e:
            print(f"[ERROR] WebSocket connection failed: {e}")
            self.reconnect()
        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}")
            self.reconnect()
    
    def identify(self):
        auth = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {
                    "$os": "Windows",
                    "$browser": "Chrome",
                    "$device": "Desktop",
                },
                "presence": {
                    "status": self.status,
                    "afk": False
                },
            },
        }
        self.ws.send(json.dumps(auth))
    
    def update_status(self):
        if custom_status:
            cstatus = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [
                        {
                            "type": 4,
                            "state": custom_status,
                            "name": "Custom Status",
                            "id": "custom",
                        }
                    ],
                    "status": self.status,
                    "afk": False,
                },
            }
            self.ws.send(json.dumps(cstatus))
    
    def heartbeat_loop(self):
        while self.should_heartbeat:
            try:
                time.sleep(self.heartbeat_interval)
                if self.ws and self.should_heartbeat:
                    heartbeat = {"op": 1, "d": None}
                    self.ws.send(json.dumps(heartbeat))
            except Exception as e:
                print(f"[ERROR] Heartbeat failed: {e}")
                break
    
    def listen(self):
        try:
            while True:
                response = self.ws.recv()
                data = json.loads(response)
                
                # Handle different opcodes
                if data["op"] == 11:  # Heartbeat ACK
                    continue
                elif data["op"] == 7:  # Reconnect
                    print("[INFO] Discord requested reconnection")
                    self.reconnect()
                    break
                elif data["op"] == 9:  # Invalid Session
                    print("[WARNING] Invalid session, reconnecting...")
                    time.sleep(5)
                    self.reconnect()
                    break
                    
        except websocket.WebSocketConnectionClosedException:
            print("[WARNING] WebSocket connection closed")
            self.reconnect()
        except Exception as e:
            print(f"[ERROR] Error in listen loop: {e}")
            self.reconnect()
    
    def reconnect(self):
        self.should_heartbeat = False
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            wait_time = min(30, 5 * self.reconnect_attempts)
            print(f"[INFO] Reconnecting in {wait_time} seconds... (Attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
            time.sleep(wait_time)
            self.connect()
        else:
            print("[ERROR] Max reconnection attempts reached. Exiting.")
            sys.exit(1)
    
    def close(self):
        self.should_heartbeat = False
        if self.ws:
            self.ws.close()

def run_bot():
    os.system("clear")
    print(f"Logged in as {username}#{discriminator} ({userid}).")
    print(f"Status: {status}")
    if custom_status:
        print(f"Custom Status: {custom_status}")
    
    bot = DiscordStatusBot(usertoken, status)
    
    try:
        bot.connect()
    except KeyboardInterrupt:
        print("\n[INFO] Bot stopped by user")
        bot.close()
    except Exception as e:
        print(f"[ERROR] Bot crashed: {e}")
        bot.close()

if __name__ == "__main__":
    keep_alive()
    run_bot()
