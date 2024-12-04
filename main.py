import re
from asyncio import Queue
import asyncio
from datetime import datetime

import aiofiles
import threading
from websockets.asyncio.client import connect
from twitchio.ext import commands
from twitchio import ChannelInfo
from chat import gpt3_completion, open_file
import time
import random
import creds
import json
from json_handler import read_json_file, write_json_file
from logger import Logger


CONVERSATION_LIMIT = 40

IGNORED_USERS = ['']
BLACKLISTED_USERS = ['']

class Memory:

    def __init__(self):
        self.store = []

    def to_string(self):
        string = ''
        for memo in self.store:
            string += memo + '\n'
        return string

    def append(self, memo):
        self.store.append(memo)

    def remove(self, memo):
        for i, m in enumerate(self.store):
            if memo == self.store[i]:
                del self.store[i]

class CustomMessage:
    author:str
    content:str
    plattform:str
    answer:bool
    
    def __init__(self,author:str,content:str, plattform:str) -> None:
        self.author = author
        self.content = content
        self.plattform = plattform
        self.answer = False


async def write_memory(memory: Memory):
    async with aiofiles.open('memory.txt', mode='w') as file:
        await file.write(memory.to_string())

async def clean_conversation(response):
    response = response.replace('_', ' ')  # replace _ with SPACE to make TTS less jarring
    if response.startswith('Sally:'):
        response = response.replace('Sally:', '')  # sometimes Sally: shows up
    if response.startswith('Sally on Twitch:'):
        response = response.replace('Sally on Twitch:', '')  # don't even...
    if response.startswith('Sally on YouTube:'):
        response = response.replace('Sally on YouTube:', '')
    if response.startswith('Sally on Stream:'):
        response = response.replace('Sally on Stream:', '')
    return response

class QueueConsumer:
    
    def __init__(self, logger:Logger, speaker_bot_port:int = 7585, no_command:bool = False, verbose:bool = False, answer_rate:int = 30, talk_to_self = False, prompt_path = 'prompt_chat.txt') -> None:
        
        self.last_answer = None
        self.redeemed_at = None
        self.msg_counter = None
        self.prompt_text = None
        self.sally_costs = 20

        self.l = logger
        self.l.passing('Spawning Consumer')
        self.verbose = verbose
        self.conversation = list()
        self.queue = Queue()
        self.prompt_path = prompt_path
        self.no_command = no_command
        self.sally_tokens = read_json_file('sally_tokens.json')
        self.port = speaker_bot_port
        self.answer_rate = answer_rate
        self.enjoy_counter = 0
        self.talk_to_self = talk_to_self
        self.is_redeemed = False
        self.cooldown = 300
        self.memory = Memory()
        self.l.info('Loading Memory:')
        for memo in open_file('memory.txt').split('\n'):
            if memo == '':
                continue
            self.l.info(f'Appending: {memo}')
            self.memory.append(memo)
        self.system_prompt = {}

    def run(self):
        self.l.passing('starting consumer')
        asyncio.run(self.main())
    
    async def main(self):
        self.l.passing('consumer started')
        self.last_answer = time.time()
        self.cooldown = 300
        await self.reload_prompt()
        self.system_prompt = {'role': 'system',
                              'content': f'{self.prompt_text} DATE:{datetime.today().strftime('%Y-%m-%d')} MEMORY: {self.memory.to_string()}'}
        try:
            while True:
                await self.youtube_chat() #check for new Youtube chat messages
                await self.voice_control() #check for new Voice commands

                while not self.queue.empty():
                    message = await self.queue.get()
                    await self.append_to_conv(message) #purge messages before checking for redeem

                if not self.is_redeemed: # check if overwrite has happened
                    self.is_redeemed = await self.check_for_redeem()
                self.msg_counter = 0
                self.redeemed_at = time.time()
                while self.is_redeemed:
                    await self.handle_redeem()

        except Exception as e:
            self.l.fail(f'Exception in main loop: {e}')

    async def handle_redeem(self):
        if self.queue.qsize() > 5:
            self.l.warning('Flushing Queue')
            while self.queue.qsize() > 1:
                message = await self.queue.get()
                await self.append_to_conv(message)  # purge messages as too many messages have accumulated
        if not self.queue.empty():
            message: CustomMessage = await self.queue.get()
            if any(message.author == user for user in IGNORED_USERS):
                self.l.warning(f'Message ignored, user on ignore list: {message.author}')
                return
            if not await self.check_completion(message):  # checks for already answered messages
                await self.request_completion(message)  # requests chatGPT completion
                await asyncio.sleep(len(message.content) / 10)
                self.last_answer = time.time()
                return
        if self.talk_to_self and (time.time() - self.last_answer) > 20:  # if more than 30secs elapsed since last message
            await self.request_completion()  # requests chatGPT completion
            self.last_answer = time.time()
            return
        await self.youtube_chat()  # check for new Youtube chat messages
        await self.voice_control()  # check for new Voice commands

        c_t = time.time()
        if (c_t - self.redeemed_at > self.cooldown) and self.queue.empty():
            self.l.passing('Cooldown reached, waiting for another redeem...')
            self.is_redeemed = False
        elif c_t - self.redeemed_at > self.cooldown:
            self.l.warning('Cooldown reached, still processing messages')
            self.msg_counter += 1
            if self.msg_counter < 10:
                self.l.warning('Cooldown reached, processed 10 messages, continuing anyway')
                self.is_redeemed = False

    async def append_to_conv(self, message:CustomMessage):
        cleaned_name = message.author.replace('_',' ')
        content = message.content.encode(encoding='ASCII',errors='ignore').decode()
        self.conversation.append({ 'role': 'user',
                                  'content': f'{cleaned_name} on {message.plattform}: {content}' })
            
    async def check_for_redeem(self):
        
        return await self.file2queue('sally_enable.txt', 'Twitch freed Sally with the message')       
    
    async def voice_control(self):
        
        await self.file2queue('streamer_exchange.txt', 'Stream')       
            
    async def youtube_chat(self):
        
        await self.file2queue('chat_exchange.txt', 'YouTube')
        
    async def file2queue(self,file_uri:str, plattform:str):
        file_contents = open_file(file_uri)
        if len(file_contents) < 1:
            return False
        lines = file_contents.split('\n')
        for line in lines:
            if len(line) <1:
                continue
            contents = line.split(';msg:')
            msg = CustomMessage(contents[0], contents[1], plattform)
            if plattform == 'YouTube':
                msg = await self.handle_sally_tokens(msg)
            if plattform == 'Twitch freed Sally with the message':
                msg.answer = True
            else:
                msg.answer = await self.response_decision(msg)
            await self.queue.put(msg)
            
            
        self.delete_file_contents(file_uri)
        
        self.l.userReply(msg.author,msg.plattform , msg.content)  
        return True

    async def handle_sally_tokens(self, message:CustomMessage) -> CustomMessage:
        await self.grant_sally_token(message.author)
        if '!sally' in message.content.lower():
            if self.sally_tokens[message.author] >= self.sally_costs and not self.is_redeemed:
                self.l.passing(f'{message.author} redeemed {message.content}')
                self.sally_tokens[message.author] -= self.sally_costs
                self.is_redeemed = True
                message.answer = True
            elif self.sally_tokens[message.author] < self.sally_costs and not self.is_redeemed: # Amount of msg to send for one enable sally: 20
                self.l.warning(f'{message.author} redeemed {message.content} with insuficcient funds')
            elif self.is_redeemed:
                self.l.warning(f'{message.author} redeemed {message.content}, while already redeemed')
        write_to_json_file(self.sally_tokens, 'sally_tokens.json')
        return message

    def delete_file_contents(self, file_path):
        try:
            # Open the file in write mode, which truncates the file
            with open(file_path, 'w'):
                pass  # Using pass to do nothing inside the with block
            self.l.passing("Contents of '{}' have been deleted.".format(file_path))
        except IOError:
            self.l.error("Unable to delete contents of '{}'.".format(file_path))

    async def grant_sally_token(self, user):
        if user in BLACKLISTED_USERS:
            self.l.fail(f'User {user} blacklisted, not granting Token')
        self.l.info(f'Granting {user} sally token')
        if user in self.sally_tokens.keys():
            self.sally_tokens[user] += 1
        else:
            self.sally_tokens[user] = 1

    async def put_message(self, message): # Only for twitch Message Objects! Not custom message
        author = message.author.name
        msg = message.content
        
        new_msg = CustomMessage(author, msg, 'Twitch')
        new_msg.answer = await self.response_decision(new_msg)
        await self.queue.put(new_msg)
        
    async def reload_prompt(self):
        self.l.passingblue('Reloading Prompt')
        self.prompt_text = open_file(self.prompt_path)

    async def toggle_verbosity(self):
        self.verbose = not self.verbose
        self.l.passingblue(f'Verbosity is now: {self.verbose}')
    
    async def clear_conv(self):
        self.l.passingblue('Clearing Conversations')
        self.conversation = list()
        
    async def check_completion(self, message: CustomMessage):
        
        for c in self.conversation:
            if c['content'] == message.content:
                return True
            
        return False
                
    async def request_completion(self,message: CustomMessage = None):
        
        if message == None:
            self.l.passingblue('Message is None, replying to self')
        else:
            # Check if the message is too long or short
            if len(message.content) > 150:
                self.l.warning('Message ignored: Too long')
                return
            if len(message.content) < 6:
                self.l.warning('Message ignored: Too short')
                return
            
            self.l.warning('--------------\nMessage being processed')
            self.l.userReply(message.author, message.plattform, message.content)
            self.l.info(self.conversation, printout = self.verbose)
            
            await self.append_to_conv(message)
            
            
            if not message.answer:
                self.l.info('Message appended, not answering', printout = self.verbose)
                return
        self.system_prompt = {'role': 'system',
                              'content': f'{self.prompt_text} DATE:{datetime.today().strftime('%Y-%m-%d')} MEMORY: {self.memory.to_string()}'}
        response:str = gpt3_completion(self.system_prompt , self.conversation, self.l, verbose = self.verbose, tokens=250)
        cleaned_response = await self.extract_memory(response)
        cleaned_response = await clean_conversation(cleaned_response)



        self.l.botReply("Sally",response)

        await self.speak(cleaned_response)

        if self.conversation.count({'role': 'assistant', 'content': response}) == 0:
            self.conversation.append({ 'role': 'assistant', 'content': response })
        
        if len(self.conversation) > CONVERSATION_LIMIT:
            self.conversation = self.conversation[1:]
        
        time.sleep(len(response)/10)
        
        self.l.warning('Cooldown ended, waiting for next message...\n--------------')

    async def extract_memory(self, msg: str) -> str:
        """
        Extracts the string encapsulated in memory{} from the given message.

        Args:
            msg (str): The input string containing the memory encapsulation.

        Returns:
            str: The extracted string inside memory{}, or an empty string if not found.
        """
        pattern = r"write_memory\{(.*?)\}"  # Regex pattern to match memory{DATA_TO_EXTRACT}
        match_write = re.search(pattern, msg)
        if match_write:
            memory = match_write.group(1)
            msg = re.sub(pattern, '', msg).strip()
            self.memory.append(memory)
            await write_memory(self.memory)

        pattern = r"delete_memory\{(.*?)\}"  # Regex pattern to match memory{DATA_TO_EXTRACT}
        match_delete = re.search(pattern, msg)
        if match_delete:
            memo = match_delete.group(1)
            msg = re.sub(pattern, '', msg).strip()
            self.memory.remove(memo)

        if match_delete or match_write:
            await write_memory(self.memory)

        return msg

    async def response_decision(self, msg:CustomMessage) -> bool:
        if self.no_command:
            self.l.info("No Command flag set")
            return True
        
        if 'sally' in msg.content.lower():
            self.l.info("Sally in msg")
            return True
        
        if '?response' in msg.content.lower():
            self.l.info("Command in msg")
            return True
        
        if random.randint(1,100)<self.answer_rate: #respond to 30% of messages anyway 
            self.l.info("Random trigger")
            return True
        
        if 'caesarlp' in msg.author:
            self.l.info("CaesarLP in msg")
            return True
        
        if 'Caesar LP' in msg.author:
            self.l.info("Caesar LP in msg")
            return True
        if 'CaesarLP' in msg.author:
            self.l.info("Caesar talked")
            return True
        self.l.warning('Ignoring message for now')
        return False

    def set_stream_info(self, game, title):
        self.l.passing(f'Setting stream info to "{title}" playing "{game}"')
        self.system_prompt['content'] = self.system_prompt['content'].replace(
            'STREAM_TITLE', title
            ).replace('GAME_NAME', game)
        
    async def speak(self, message):
        
        _id = random.randrange(10000,99999)
        if 'enjoy' in message:
            self.enjoy_counter += 1
        data = {
            "request": "Speak",
            "id": f"{_id}",
            "voice": "Sally",
            "message": f"{message}"
            }
        
        self.l.info(f"Sending Packet with ID {_id}")
        self.l.warning(f'Enjoy counter is {self.enjoy_counter}')
        
        await self.send_json_via_websocket(data)

    async def send_json_via_websocket(self, json_data):
        websocket = await connect(f'ws://localhost:{self.port}/speak')
        try:
            # Convert JSON data to string
            json_string = json.dumps(json_data)

            # Send JSON string via WebSocket
            await websocket.send(json_string)
            self.l.info("Sent JSON data")
            if self.verbose:
                self.l.info(json_string)
        except Exception as e:
            self.l.fail(f'Failed to send JSON data: {e}')


class Bot(commands.Bot):

    def __init__(self, consumer: QueueConsumer, logger: Logger, no_command:bool = False):
        # Initialise our Bot with our access token, prefix and a list of channels to join on boot...
        # prefix can be a callable, which returns a list of strings or a string...
        # initial_channels can also be a callable which returns a list of strings...
        self.l = logger
        self.l.passingblue('Spawning Bot')
        self.queueConsumer = consumer
        self.no_command = no_command
        super().__init__(token= creds.TWITCH_TOKEN, prefix='?', initial_channels=[creds.TWITCH_CHANNEL])

    async def event_ready(self):
        # Notify us when everything is ready!
        # We are logged in and ready to chat and use commands...
        self.l.passing(f'Logged in as | {self.nick}')
        
        await self.update_stream_info()
        
        
        
    #returns true if response should be given
    
    async def update_stream_info(self):
        ch:ChannelInfo  = await self.fetch_channel(self.nick)
        game = ch.game_name
        title_parts = ch.title.split('|')
        title = title_parts[0]
        self.queueConsumer.set_stream_info(game, title)    

    async def event_message(self, message):
        # Messages with echo set to True are messages sent by the bot...
        # For now we just want to ignore them...
        self.l.info('Message recieved:')
        
        #if message.echo:
        #    return
        if message.author.name == 'caesarlp':
        
            if '!reload_prompt' in message.content:
                self.l.warning('Reloading prompt')
                
                await self.queueConsumer.reload_prompt()
                return
            
            if '!toggle_verbose' in message.content:
                self.l.warning('Toggling Verbosity')
                
                await self.queueConsumer.toggle_verbosity()
                return
            
            if '!clear_conv' in message.content:
                self.l.warning('Clearing Conversation')
                
                await self.queueConsumer.clear_conv()
                return
            
            if '!update_info'in message.content:
                self.l.warning('Updating Info')
                await self.update_stream_info()
                await self.queueConsumer.reload_prompt()
                return
            
            if '!reload_all'in message.content:
                self.l.warning('Reloading everything')
                await self.update_stream_info()
                await self.queueConsumer.reload_prompt()
                await self.queueConsumer.clear_conv()
                return
            if '!enable_sally'in message.content:
                self.l.warning('Enabling Sally')
                self.queueConsumer.cooldown = 999999
                self.queueConsumer.is_redeemed = True
                return
            if '!disable_sally'in message.content:
                self.l.warning('Disabling Sally')
                self.queueConsumer.cooldown = 300
                self.queueConsumer.is_redeemed = False
                return
                
        
        msg: str = f'{message.author.name}:  {message.content}'    
        self.l.info(msg)
        await self.queueConsumer.put_message(message)
        
        
        
        await self.handle_commands(message)
        
        
        

  

if __name__ == '__main__':


    l = Logger(console_log=True, file_logging=True, file_URI='logs/logger.txt', override=True)
    
    consumer = QueueConsumer(logger=l, verbose=True, answer_rate=20, talk_to_self=False, prompt_path='prompt_chat.txt')
    bot = Bot(consumer, l)
    process = threading.Thread(target=consumer.run)
    process.start()
    


    bot.run()
    process.join()



