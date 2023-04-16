from dotenv import load_dotenv
from flask import Flask, request, abort
from waitress import serve
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, AudioMessage
)
import os
import uuid

from src.models import OpenAIModel
from src.memory import Memory
from src.logger import logger
from src.storage import Storage, FileStorage, MongoStorage
from src.utils import get_role_and_content
from src.service.youtube import Youtube, YoutubeTranscriptReader
from src.service.website import Website, WebsiteReader
from src.mongodb import mongodb

load_dotenv('.env')

app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
default_open_ai_token = os.getenv('DEFAULT_OPEN_AI_TOKEN')
storage = None
youtube = Youtube(step=4)
website = Website()


memory = Memory(system_message=os.getenv('SYSTEM_MESSAGE'), memory_message_count=2)
model_management = {}
api_keys = {}

def setup_token(user_id: str, api_key:str):
    model = OpenAIModel(api_key=api_key)
    is_successful, _, _ = model.check_token_valid()
    if not is_successful:
        raise ValueError('Invalid API token')
    model_management[user_id] = model
    storage.save({
        user_id: api_key
    })

def get_model(user_id: str):
    if user_id in model_management:
        return model_management[user_id]
    else:
        if not default_open_ai_token:
            logger.error("invalid system token")
            raise KeyError()
        setup_token(user_id, default_open_ai_token)
        return model_management[user_id]

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    logger.info(f'{user_id}: {text}')

    try:
        def setup_token(api_key:str):
            model = OpenAIModel(api_key=api_key)
            is_successful, _, _ = model.check_token_valid()
            if not is_successful:
                raise ValueError('Invalid API token')
            model_management[user_id] = model
            storage.save({
                user_id: api_key
            })
        if text.startswith('/token'):
            api_key = text[3:].strip()
            setup_token(user_id, api_key)
            msg = TextSendMessage(text='Token Enabled.')

        elif text.startswith('/help'):
            msg = TextSendMessage(text="説明：\n/token + API Token\n👉API Tokenは、https://platform.openai.com/ に登録することで取得できます。\n\n/system + Prompt\n👉 Prompt 要約が得意な人になってもらうなど、ある役割をロボットに命令することができます\n\n/clear\n👉 現在、それぞれのケースで過去2回の履歴が記録されていますが、このコマンドは履歴情報をクリアするものです。\n\n/image + Prompt\n👉 DALL∙E 2 モデルを使ってテキストから画像を生成します。\n\n音声入力\n👉 Whisperモデルが呼び出されて音声がテキストに変換され、次にChatGPTが呼び出されてテキストで返信されます。\n\nその他のテキスト入力\n👉 ChatGPTに文字を入力")

        elif text.startswith('/system'):
            memory.change_system_message(user_id, text[5:].strip())
            msg = TextSendMessage(text='システムプロンプトを入力しました。')

        elif text.startswith('/clear'):
            memory.remove(user_id)
            msg = TextSendMessage(text='履歴のクリアに成功しました。')

        elif text.startswith('/image'):
            prompt = text[3:].strip()
            memory.append(user_id, 'user', prompt)
            is_successful, response, error_message = get_model(user_id).image_generations(prompt)
            if not is_successful:
                raise Exception(error_message)
            url = response['data'][0]['url']
            msg = ImageSendMessage(
                original_content_url=url,
                preview_image_url=url
            )
            memory.append(user_id, 'assistant', url)

        else:
            user_model = get_model(user_id)
            memory.append(user_id, 'user', text)
            url = website.get_url_from_text(text)
            if url:
                if youtube.retrieve_video_id(text):
                    is_successful, chunks, error_message = youtube.get_transcript_chunks(youtube.retrieve_video_id(text))
                    if not is_successful:
                        raise Exception(error_message)
                    youtube_transcript_reader = YoutubeTranscriptReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                    is_successful, response, error_message = youtube_transcript_reader.summarize(chunks)
                    if not is_successful:
                        raise Exception(error_message)
                    role, response = get_role_and_content(response)
                    msg = TextSendMessage(text=response)
                else:
                    chunks = website.get_content_from_url(url)
                    if len(chunks) == 0:
                        raise Exception('このサイトからテキストを取得できませんでした。')
                    website_reader = WebsiteReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                    is_successful, response, error_message = website_reader.summarize(chunks)
                    if not is_successful:
                        raise Exception(error_message)
                    role, response = get_role_and_content(response)
                    msg = TextSendMessage(text=response)
            else:
                is_successful, response, error_message = user_model.chat_completions(memory.get(user_id), os.getenv('OPENAI_MODEL_ENGINE'))
                if not is_successful:
                    raise Exception(error_message)
                role, response = get_role_and_content(response)
                msg = TextSendMessage(text=response)
            memory.append(user_id, role, response)
    except ValueError:
        msg = TextSendMessage(text='Token が無効です。以下のフォーマットで入力してください。 /token sk-xxxxx')
    except KeyError:
        msg = TextSendMessage(text='トークンを先に登録してください。/token sk-xxxxx の形式で登録してください。')
    except Exception as e:
        memory.remove(user_id)
        if str(e).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token が正しくありません。/token sk-xxxxx の形式で登録してください。')
        elif str(e).startswith('That model is currently overloaded with other requests.'):
            msg = TextSendMessage(text='同時使用人数を超えました。しばらく待ってからお試しください。')
        else:
            msg = TextSendMessage(text=str(e))
    line_bot_api.reply_message(event.reply_token, msg)


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    user_id = event.source.user_id
    audio_content = line_bot_api.get_message_content(event.message.id)
    input_audio_path = f'{str(uuid.uuid4())}.m4a'
    with open(input_audio_path, 'wb') as fd:
        for chunk in audio_content.iter_content():
            fd.write(chunk)

    try:
        is_successful, response, error_message = get_model(user_id).audio_transcriptions(input_audio_path, 'whisper-1')
        if not is_successful:
            raise Exception(error_message)
        memory.append(user_id, 'user', response['text'])
        is_successful, response, error_message = get_model(user_id).chat_completions(memory.get(user_id), 'gpt-3.5-turbo')
        if not is_successful:
            raise Exception(error_message)
        role, response = get_role_and_content(response)
        memory.append(user_id, role, response)
        msg = TextSendMessage(text=response)
    except ValueError:
        msg = TextSendMessage(text='最初に /token sk-xxxxx の形式でトークンを登録してください。')
    except KeyError:
        msg = TextSendMessage(text='最初に /token sk-xxxxx の形式でトークンを登録してください。')
    except Exception as e:
        memory.remove(user_id)
        if str(e).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token が正しくありません。/token sk-xxxxx の形式で登録してください。')
        else:
            msg = TextSendMessage(text=str(e))
    os.remove(input_audio_path)
    line_bot_api.reply_message(event.reply_token, msg)


@app.route("/", methods=['GET'])
def home():
    return 'Hello World'


if __name__ == "__main__":
    if os.getenv('USE_MONGO'):
        mongodb.connect_to_database()
        storage = Storage(MongoStorage(mongodb.db))
    else:
        storage = Storage(FileStorage('db.json'))
    try:
        data = storage.load()
        for user_id in data.keys():
            model_management[user_id] = OpenAIModel(api_key=data[user_id])
    except FileNotFoundError:
        pass
    # app.run(host='0.0.0.0', port=8080)
    serve(app, host='0.0.0.0', port=8080)
