from enum import Enum
from typing import List, Dict
import requests


class ModelInterface:
    def check_token_valid(self) -> bool:
        pass

    def chat_completions(self, messages: List[Dict], model_engine: str) -> str:
        pass

    def audio_transcriptions(self, file, model_engine: str) -> str:
        pass

    def image_generations(self, prompt: str) -> str:
        pass


class OpenAIModelCmd(Enum):
    NONE = 1
    SET_TOKEN = 2
    SET_SYSTEM_PROMPT = 3
    SET_IMAGE_PROMPT = 4
    SET_SUMMARIZE_URL = 5
class OpenAIModel(ModelInterface):

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = 'https://api.openai.com/v1'
        self.cmd = OpenAIModelCmd.NONE

    def _request(self, method, endpoint, body=None, files=None):
        self.headers = {
            'Authorization': f'Bearer {self.api_key}'
        }
        try:
            if method == 'GET':
                r = requests.get(f'{self.base_url}{endpoint}', headers=self.headers)
            elif method == 'POST':
                if body:
                    self.headers['Content-Type'] = 'application/json'
                r = requests.post(f'{self.base_url}{endpoint}', headers=self.headers, json=body, files=files)
            r = r.json()
            if r.get('error'):
                return False, None, r.get('error', {}).get('message')
        except Exception:
            return False, None, 'OpenAI API システムが不安定なため、後で再試行してください。'
        return True, r, None

    def check_token_valid(self):
        return self._request('GET', '/models')

    def chat_completions(self, messages, model_engine) -> str:
        json_body = {
            'model': model_engine,
            'messages': messages
        }
        return self._request('POST', '/chat/completions', body=json_body)

    def audio_transcriptions(self, file_path, model_engine) -> str:
        files = {
            'file': open(file_path, 'rb'),
            'model': (None, model_engine),
        }
        return self._request('POST', '/audio/transcriptions', files=files)

    def image_generations(self, prompt: str) -> str:
        json_body = {
            "prompt": prompt,
            "n": 1,
            "size": "512x512"
        }
        return self._request('POST', '/images/generations', body=json_body)

    def set_command(self, cmd: OpenAIModelCmd):
        self.cmd = cmd
    
    def pop_command(self) -> OpenAIModelCmd:
        cmd = self.cmd
        self.cmd = OpenAIModelCmd.NONE
        return cmd