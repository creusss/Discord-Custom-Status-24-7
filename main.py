import os
import sys
import json
import time
import requests
import websocket
import threading
import ssl
from keep_alive import keep_alive

status = os.getenv("status", "online")  # online/dnd/idle
custom_status = os.getenv("custom_status")  # Custom status text
usertoken = os.getenv("token")

if not usertoken:
    print("[ERROR] Please add a token inside Secrets.")
    sys.exit()

headers = {"Authorization": usertoken, "Content-Type": "application/json"}

# Validate token
try:
    validate = requests.get("https://discord.com/api/v9/users/@me", headers=headers, timeout=10)
    if validate.status_code != 200:
        print("[ERROR] Your token might be invalid. Please check it again.")
        print(f"[DEBUG] Status code: {validate.status_code}")
        sys.exit()
except requests.exceptions.RequestException as e:
    print(f"[ERROR] Failed to validate token: {e}")
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
        self.sequence = None
        self.session_id = None
        
    def connect(self):
        try:
            # Enable SSL debugging for troubleshooting
            websocket.enableTrace(False)  # Set to True for debugging
            
            # Create WebSocket with SSL context
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            self.ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
            self.ws.settimeout(30)
            
            # Connect with proper headers
            self.ws.connect(
                "wss://gateway.discord.gg/?v=9&encoding=json",
                header={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
            )
            
            print("[INFO] WebSocket connected successfully")
            
            # Receive hello event
            hello_data = self.ws.recv()
            hello = json.loads(hello_data)
            
            if hello.get("op") != 10:
                print(f"[ERROR] Expected hello event (op 10), got: {hello}")
                return False
                
            self.heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
            print(f"[INFO] Heartbeat interval: {self.heartbeat_interval}s")
            
            # Start heartbeat in separate thread
            self.should_heartbeat = True
            self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop)
            self.heartbeat_thread.daemon = True
            self.heartbeat_thread.start()
            
            # Send identification
            self.identify()
            
            # Listen for events
            self.listen()
            
        except ssl.SSLError as e:
            print(f"[ERROR] SSL Error: {e}")
            self.reconnect()
        except websocket.WebSocketException as e:
            print(f"[ERROR] WebSocket connection failed: {e}")
            self.reconnect()
        except Exception as e:
            print(f"[ERROR] Unexpected error during connection: {e}")
            self.reconnect()
    
    def safe_send(self, data):
        """Safely send data through WebSocket with error handling"""
        try:
            if self.ws and self.ws.connected:
                self.ws.send(json.dumps(data))
                return True
            else:
                print("[WARNING] WebSocket not connected, cannot send data")
                return False
        except ssl.SSLError as e:
            print(f"[ERROR] SSL error while sending: {e}")
            self.reconnect()
            return False
        except websocket.WebSocketConnectionClosedException:
            print("[WARNING] Connection closed while sending data")
            self.reconnect()
            return False
        except Exception as e:
            print(f"[ERROR] Error while sending data: {e}")
            return False
    
    def identify(self):
        auth = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {
                    "$os": "linux",
                    "$browser": "my_library",
                    "$device": "my_library",
                },
                "presence": {
                    "status": self.status,
                    "afk": False
                },
            },
        }
        
        if self.safe_send(auth):
            print("[INFO] Identification sent")
        else:
            print("[ERROR] Failed to send identification")
    
    def update_status(self):
        activities = []
        if custom_status:
            activities.append({
                "type": 4,
                "state": custom_status,
                "name": "Custom Status",
                "id": "custom",
            })
        
        status_update = {
            "op": 3,
            "d": {
                "since": 0,
                "activities": activities,
                "status": self.status,
                "afk": False,
            },
        }
        
        if self.safe_send(status_update):
            print(f"[INFO] Status updated to '{self.status}'")
            if custom_status:
                print(f"[INFO] Custom status set to '{custom_status}'")
        else:
            print("[ERROR] Failed to update status")
    
    def heartbeat_loop(self):
        while self.should_heartbeat:
            try:
                time.sleep(self.heartbeat_interval)
                if self.ws and self.should_heartbeat:
                    heartbeat = {"op": 1, "d": self.sequence}
                    if not self.safe_send(heartbeat):
                        print("[ERROR] Failed to send heartbeat")
                        break
            except Exception as e:
                print(f"[ERROR] Heartbeat loop error: {e}")
                break
    
    def listen(self):
        try:
            while True:
                try:
                    response = self.ws.recv()
                    if not response:
                        print("[WARNING] Received empty response")
                        continue
                        
                    data = json.loads(response)
                    
                    # Update sequence number
                    if data.get("s"):
                        self.sequence = data["s"]
                    
                    # Handle different opcodes
                    if data["op"] == 0:  # Dispatch
                        if data["t"] == "READY":
                            self.session_id = data["d"]["session_id"]
                            print(f"[INFO] Bot ready! Session ID: {self.session_id}")
                            # Update status after ready
                            time.sleep(1)  # Small delay
                            self.update_status()
                        elif data["t"] == "RESUMED":
                            print("[INFO] Session resumed")
                            
                    elif data["op"] == 11:  # Heartbeat ACK
                        continue
                        
                    elif data["op"] == 7:  # Reconnect
                        print("[INFO] Discord requested reconnection")
                        self.reconnect()
                        break
                        
                    elif data["op"] == 9:  # Invalid Session
                        print("[WARNING] Invalid session")
                        if data.get("d"):
                            print("[INFO] Session is resumable")
                        else:
                            print("[INFO] Session is not resumable, starting fresh")
                            self.session_id = None
                        time.sleep(5)
                        self.reconnect()
                        break
                        
                except json.JSONDecodeError as e:
                    print(f"[ERROR] Failed to decode JSON: {e}")
                    continue
                    
        except websocket.WebSocketConnectionClosedException:
            print("[WARNING] WebSocket connection closed")
            self.reconnect()
        except ssl.SSLError as e:
            print(f"[ERROR] SSL error in listen loop: {e}")
            self.reconnect()
        except Exception as e:
            print(f"[ERROR] Error in listen loop: {e}")
            self.reconnect()
    
    def reconnect(self):
        print("[INFO] Starting reconnection process...")
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
        print("[INFO] Closing bot...")
        self.should_heartbeat = False
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=2)
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

def run_bot():
    os.system("clear")
    print("=" * 50)
    print("Discord Status Bot")
    print("=" * 50)
    print(f"Logged in as {username}#{discriminator} ({userid})")
    print(f"Status: {status}")
    if custom_status:
        print(f"Custom Status: {custom_status}")
    print("=" * 50)
    
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
