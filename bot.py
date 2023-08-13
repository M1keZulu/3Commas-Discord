import discord
import websocket
import asyncio
import time
from datetime import datetime, timedelta
import threading
import json
import hashlib
import hmac
import requests
import os
import google.cloud.logging
import logging

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
ALLOWED_ROLE_NAME = os.environ.get("ALLOWED_ROLE_NAME")
BACKUP_DISK_PATH = os.environ.get("BACKUP_DISK_PATH")

message_queue = asyncio.Queue()

class WebSocketClient:
    def __init__(self):
        self.url = "wss://ws.3commas.io/websocket"
        self.ws = None
        self.client_list = []
        self.connection_lost=None
    
    def get_time_diff(self, timestamp):
        now = datetime.utcnow()
        input_time = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        delta = now - input_time

        if delta < timedelta(minutes=1):
            return "just now"
        elif delta < timedelta(hours=1):
            minutes = delta.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif delta < timedelta(days=1):
            hours = delta.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif delta < timedelta(weeks=1):
            days = delta.days
            return f"{days} day{'s' if days != 1 else ''} ago"
        elif delta < timedelta(days=30):
            weeks = delta.days // 7
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"
        else:
            months = delta.days // 30
            return f"{months} month{'s' if months != 1 else ''} ago"

    def subscribe(self, name, api_key, secret_key):
        identifier = {
                "channel": 'DealsChannel',
                "users": [
                {
                    "api_key": api_key,
                    "signature": hmac.new(secret_key.encode(), b"/deals", hashlib.sha256).hexdigest()
                }
                ],
        }
        self.ws.send(json.dumps({"identifier": json.dumps(identifier), "command": "subscribe"}))
        self.client_list.append({"name": name, "api_key": api_key, "secret_key": secret_key})
    
    def unsubscribe(self, name):
        for client in self.client_list:
            if client['name'] == name:
                self.client_list.remove(client)
                self.ws.close()
                logging.info(f"Removed {name} from the list of clients.")
                return

    def on_message(self, ws, recv_message):
        data=json.loads(recv_message)
        identifier = None

        if 'identifier' in data:
            identifier = json.loads(data['identifier'])

        if 'message' in data:
            message = data['message']
        
        if 'type' in data and data['type'] == 'confirm_subscription':
            for client in self.client_list:
                if client['api_key'] == identifier['users'][0]['api_key'] and hmac.new(client['secret_key'].encode(), b"/deals", hashlib.sha256).hexdigest() == identifier['users'][0]['signature']:
                    message_queue.put_nowait(f"Subscription with {client['name']} confirmed.")
                    break
        elif 'type' in data and data['type'] == 'reject_subscription':
            for client in self.client_list:
                if client['api_key'] == identifier['users'][0]['api_key'] and hmac.new(client['secret_key'].encode(), b"/deals", hashlib.sha256).hexdigest() == identifier['users'][0]['signature']:
                    message_queue.put_nowait(f"Subscription with {client['name']} rejected.")
                    self.client_list.remove(client)
                    break
        elif 'message' in data and 'type' in str(message) and message['type'] == 'Deal':
            pair = message['pair']
            pair = pair.replace('_', '/')
            name = ""
            for closed_event in message['bot_events']:
                if "Closed at" in closed_event['message']:
                    msg = closed_event['message']
                    for client in self.client_list:
                        if client['api_key'] == identifier['users'][0]['api_key'] and hmac.new(client['secret_key'].encode(), b"/deals", hashlib.sha256).hexdigest() == identifier['users'][0]['signature']:
                            name = client['name']
                            break
                    message_queue.put_nowait(f"{name}: {pair} {msg} with MARCO POLO")

    def on_error(self, ws, error):
        logging.info(f"Error from {self.url}: {error}")
        if error=='Connection to remote host was lost':
            self.connection_lost = datetime.utcnow().isoformat()[:-3] + 'Z'

    def on_close(self, ws, close_status_code, close_msg):
        logging.info(f"Connection closed for {self.url}.")

    def on_open(self, ws):
        for client in self.client_list:
            identifier = {
                "channel": 'DealsChannel',
                "users": [
                {
                    "api_key": client['api_key'],
                    "signature": hmac.new(client['secret_key'].encode(), b"/deals", hashlib.sha256).hexdigest()
                }
                ],
            }
            self.ws.send(json.dumps({"identifier": json.dumps(identifier), "command": "subscribe"}))
        logging.info(f"Connected to {self.url}")
        if self.connection_lost:
            try:
                current_time = datetime.utcnow().isoformat()[:-3] + 'Z'
                for client in self.client_list:
                    request = requests.get('https://api.3commas.io/public/api/ver1/deals?scope=finished&order=closed_at',
               headers={'Apikey': client['api_key'],
                        'Signature': hmac.new(client['secret_key'].encode(), b"/public/api/ver1/deals?scope=finished&order=closed_at", hashlib.sha256).hexdigest(),}
                        )
                    if request.status_code == 200:
                        messages = request.json()
                        for message in messages:
                            emoji = '💰' if float(message['actual_profit']) > 0 else '😱'
                            currency=""
                            if message['profit_currency'] == 'quote_currency':
                                currency = message['from_currency']
                            else:
                                currency=message['to_currency']
                            if message['localized_status'] == 'Closed at Market Price' and message['closed_at'] >= self.connection_lost and message['closed_at'] < current_time:
                                message = f"{message['pair'].replace('_','/')} {message['localized_status']} {emoji} {message['actual_profit']} {currency} ({message['actual_usd_profit']} $) ({message['actual_profit_percentage']}% from total volume) #market {self.get_time_diff(message['created_at'])} with MARCO POLO"
                                message_queue.put_nowait(message)
                self.connection_lost = None
            except Exception as e:
                logging.info(f"Error: {e}")
                logging.info("Unable to retrieve messages via API.")
                self.connection_lost = None


    def connect_to_websocket(self):
        self.ws = websocket.WebSocketApp(self.url,
                                         on_message=self.on_message,
                                         on_error=self.on_error,
                                         on_close=self.on_close,
                                         on_open=self.on_open)
        thread = threading.Thread(target=self.ws.run_forever, kwargs={'reconnect': 1, 'ping_interval': 60})
        thread.daemon = True
        thread.start()

    def run(self):
        self.connect_to_websocket()

class DiscordBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, intents=discord.Intents.all())
        self.websocket_clients = []
        self.channels = []
        self.client = None
        self.confirmation_message = False

    async def send_message_to_channels(self, message):
        for channel in self.channels:
            await channel.send(message)

    async def send_message(self):
        while(True):
            try: 
                message = await message_queue.get()
                if message:
                    logging.info(message)
                    if self.confirmation_message and message.startswith("Subscription with"):
                        await self.send_message_to_channels(message)
                    elif not message.startswith("Subscription with"):
                        await self.send_message_to_channels(message)
                    message_queue.task_done()
            except Exception as e:
                logging.info(f"Error: {e}")
                pass
            
    async def on_ready(self):
        self.client = WebSocketClient()
        if os.path.exists(f'{BACKUP_DISK_PATH}/clients.json') and os.path.exists(f'{BACKUP_DISK_PATH}/channels.json'):
            with open(f'{BACKUP_DISK_PATH}/clients.json', 'r') as f:
                    clients = json.loads(f.read())
            with open(f'{BACKUP_DISK_PATH}/channels.json', 'r') as f:
                channels = json.loads(f.read())
            self.client.client_list = []
            self.channels = []
            for client in clients:
                self.client.client_list.append(client)
            for channel_id, guild_id in channels:
                guild = discord.utils.get(self.guilds, id=guild_id)
                if guild:
                    channel = discord.utils.get(guild.channels, id=channel_id)
                    if channel:
                        self.channels.append(channel)
        self.client.run()
        logging.info(f'We have logged in as {self.user}')

    async def on_message(self, message):
        if message.author == self.user:
            return

        if not message.content.startswith('!'):
            return

        allowed_role = discord.utils.get(message.guild.roles, name=ALLOWED_ROLE_NAME)
        if allowed_role and allowed_role in message.author.roles:
            if message.content.startswith('!subscribe'):
                name, api_key, secret_key = message.content.split()[1], message.content.split()[2], message.content.split()[3]
                for client in self.client.client_list:
                    if client['name'] == name or client['api_key'] == api_key or client['secret_key'] == secret_key:
                        await message.channel.send(f"Subscription with provided credentials already exists.")
                        return
                self.client.subscribe(name, api_key, secret_key)
                await message.channel.send(f"Trying to connect with {name}. If the connection is not successful, you will be notified.")


            elif message.content.startswith('!unsubscribe'):
                name = message.content.split()[1]
                for client in self.client.client_list:
                    if client['name'] == name:
                        self.client.unsubscribe(name)
                        await message.channel.send(f"Subscription with {name} stopped.")
                        self.client.run()
                        return
                await message.channel.send(f"Subscription with {name} not found.")

            elif message.content.startswith('!ping'):
                await message.channel.send("Pong!")

            elif message.content.startswith('!list_subscriptions'):
                if self.client.client_list:
                    await message.channel.send(f"Active Subscriptions: {', '.join([client['name'] for client in self.client.client_list])}")
                else:
                    await message.channel.send("No Active Subscriptions.")

            elif message.content.startswith('!add_channel'):
                channel_name = message.content.split()[1]
                channel = discord.utils.get(message.guild.channels, name=channel_name)
                if channel:
                    self.channels.append(channel)
                    await message.channel.send(f"Added {channel_name} to the list of channels to send messages to.")
                else:
                    await message.channel.send(f"Channel {channel_name} not found.")

            elif message.content.startswith('!remove_channel'):
                channel_name = message.content.split()[1]
                channel = discord.utils.get(message.guild.channels, name=channel_name)
                if channel:
                    self.channels.remove(channel)
                    await message.channel.send(f"Removed {channel_name} from the list of channels to send messages to.")
                else:
                    await message.channel.send(f"Channel {channel_name} not found.")

            elif message.content.startswith('!list_channels'):
                channel_names = []
                for channel in self.channels:
                    if channel.guild == message.guild:
                        channel_names.append(channel.name)
                if channel_names:
                    await message.channel.send(f"Channels to send messages to: {', '.join(channel_names)}")
                else:
                    await message.channel.send("No channels to send messages to.")

            elif message.content.startswith('!show_ip'):
                ip = requests.get('https://api.ipify.org').text
                await message.channel.send(f"IP: {ip}")

            elif message.content.startswith('!enable_confirmation'):
                self.confirmation_message = True
                await message.channel.send("Enabled confirmation message.")
            
            elif message.content.startswith('!disable_confirmation'):
                self.confirmation_message = False
                await message.channel.send("Disabled confirmation message.")

            elif message.content.startswith('!backup'):
                with open(f'{BACKUP_DISK_PATH}/clients.json', 'w') as f:
                    f.write(json.dumps(self.client.client_list))
                with open(f'{BACKUP_DISK_PATH}/channels.json', 'w') as f:
                    f.write(json.dumps([(channel.id, channel.guild.id) for channel in self.channels]))
                await message.channel.send("Backed up clients and channels and guilds to file.")

            elif message.content.startswith('!restore'):
                if not os.path.exists(f'{BACKUP_DISK_PATH}/clients.json') or not os.path.exists(f'{BACKUP_DISK_PATH}/channels.json'):
                    await message.channel.send("No backup found.")
                    return
                with open(f'{BACKUP_DISK_PATH}/clients.json', 'r') as f:
                    clients = json.loads(f.read())
                with open(f'{BACKUP_DISK_PATH}/channels.json', 'r') as f:
                    channels = json.loads(f.read())
                self.client.client_list = []
                self.channels = []
                for client in clients:
                    self.client.subscribe(client['name'], client['api_key'], client['secret_key'])
                for channel_id, guild_id in channels:
                    guild = discord.utils.get(self.guilds, id=guild_id)
                    if guild:
                        channel = discord.utils.get(guild.channels, id=channel_id)
                        if channel:
                            self.channels.append(channel)
                await message.channel.send("Restored clients and channels from file.")

            elif message.content.startswith('!help'):
                help_message = (
                    "!subscribe <name> <api_key> <secret_key>: Subscribe to a new account.\n"
                    "!unsubscribe <name>: Unsubscribe from an account.\n"
                    "!list_subscriptions: List all active subscriptions.\n"
                    "!add_channel <channel_name>: Add a channel to send messages to.\n"
                    "!remove_channel <channel_name>: Remove a channel to send messages to.\n"
                    "!list_channels: List all channels to send messages to.\n"
                    "!show_ip: Show the IP of the server.\n"
                    "!enable_confirmation: Enable confirmation message.\n"
                    "!disable_confirmation: Disable confirmation message.\n"
                    "!backup: Backup clients and channels to file.\n"
                    "!restore: Restore clients and channels from file.\n"
                    "!help: Show this message.\n"
                )
                await message.channel.send(help_message)
        else:
            if self.confirmation_message:
                await message.channel.send("You don't have permission to use bot commands.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if int(os.getenv("PRODUCTION", 0)) == 1:
        logging_client = google.cloud.logging.Client()
        logging_client.setup_logging()
        logging.info("Logging client setup.")

    bot = DiscordBot()
    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(DISCORD_BOT_TOKEN))
    loop.create_task(bot.send_message())
    loop.run_forever()
