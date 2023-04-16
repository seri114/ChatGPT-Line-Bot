import os
import re
import requests
from bs4 import BeautifulSoup


WEBSITE_SYSTEM_MESSAGE = "あなたは今、データの整理、要約、まとめ、集約が得意で、細部に着目してポイントを押さえることができます。"
WEBSITE_MESSAGE_FORMAT = """
    リンク先の内容:
    \"\"\"
    {}
    \"\"\"

    いくつかのポイントに着目してください：
    1.サイトの主要な目的は何か？
    2.サイトの要約は何か？
    3.サイトに含まれる情報の中で、最も重要な視点は点は何ですか？

    この形式で回答してください:
    - 目的： '...'
    - 要約： '...'
    - 重要な視点： '...'
"""


class Website:
    def get_url_from_text(self, text: str):
        url_regex = re.compile(r'^https?://\S+')
        match = re.search(url_regex, text)
        if match:
            return match.group()
        else:
            return None

    def get_content_from_url(self, url: str):
        hotpage = requests.get(url)
        main = BeautifulSoup(hotpage.text, 'html.parser')
        chunks = [article.text.strip() for article in main.find_all('article')]
        if chunks == []:
            chunks = [article.text.strip() for article in main.find_all('div', class_='content')]
        return chunks


class WebsiteReader:
    def __init__(self, model=None, model_engine=None):
        self.system_message = os.getenv('WEBSITE_SYSTEM_MESSAGE') or WEBSITE_SYSTEM_MESSAGE
        self.message_format = os.getenv('WEBSITE_MESSAGE_FORMAT') or WEBSITE_MESSAGE_FORMAT
        self.model = model
        self.text_length_limit = 1800
        self.model_engine = model_engine

    def send_msg(self, msg):
        return self.model.chat_completions(msg, self.model_engine)

    def summarize(self, chunks):
        text = '\n'.join(chunks)[:self.text_length_limit]
        msgs = [{
            "role": "system", "content": self.system_message
        }, {
            "role": "user", "content": self.message_format.format(text)
        }]
        return self.send_msg(msgs)
