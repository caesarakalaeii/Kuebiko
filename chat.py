import openai
from openai import OpenAI

client = OpenAI(api_key=creds.OPENAI_API_KEY)
import creds
from logger import Logger


def open_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as infile:
        return infile.read()


# TODO: The 'openai.api_base' option isn't read in the client API. You will need to pass it when you instantiate the client, e.g. 'OpenAI(base_url='https://api.openai.com/v1/chat')'
# openai.api_base = 'https://api.openai.com/v1/chat'


def gpt3_completion(system_prompt, messages,logger:Logger,verbose = False, engine='gpt-4o', temp=1.1, tokens=400, freq_pen=2.0, pres_pen=2.0, stop=['SALLY:', 'CHATTER:', 'CHATTER_NAME']):
    msg = list()
    msg.append(system_prompt)
    for m in messages:
        msg.append(m)
    logger.info(msg, verbose)
    response = client.completions.create(model=engine,
    messages=msg,
    temperature=temp,
    max_tokens=tokens,
    frequency_penalty=freq_pen,
    presence_penalty=pres_pen,
    stop=stop)
    text = response.choices[0].message.content.strip()
    return text