
from normal_sally import QueueConsumer
from logger import Logger
import asyncio




async def talk():
    while True:
        message = input("Message to send:\n")
        await consumer.speak(message)

if __name__ == '__main__':


    l = Logger(console_log=True, file_logging=True, file_URI='logs/logger.txt', override=True)
    
    consumer = QueueConsumer(logger=l, verbose=True, answer_rate=20, talk_to_self=False, prompt_path='prompt_chat.txt')
    
    asyncio.run(talk())
