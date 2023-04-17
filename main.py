import copy
import textwrap
from dotenv import load_dotenv
import re
from flask import Flask, request, abort
from waitress import serve
import json
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, AudioMessage, QuickReplyButton, MessageAction, QuickReply
)
import os
import uuid

from src.models import OpenAIModel, OpenAIModelCmd
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

def get_model(user_id: str) -> OpenAIModel:
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
    text = str(event.message.text.strip())
    logger.info(f'{user_id}: {text}')

    def get_reply_and_reply_samples(string_with_json: str):

        # 正規表現パターン
        pattern = r'{[\s\S]*}'

        # 正規表現にマッチする部分を抽出
        match = re.search(pattern, string_with_json)

        if match:
            json_data = match.group()
        else:
            return string_with_json, []
        # JSONデータをPythonオブジェクトに変換
        parsed_json = json.loads(json_data)

        # replyとreply_samplesの取得
        reply = parsed_json['reply']
        reply_samples = [parsed_json[key] for key in parsed_json.keys() if key.startswith('reply sample')]
        reply_samples = list(filter(lambda x: len(x)>0, reply_samples))
        return reply, reply_samples

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
        cmd = get_model(user_id).pop_command()
        if text in ['/cancel']:
            msg = TextSendMessage(text=f'キャンセルしました')
        elif cmd == OpenAIModelCmd.SET_TOKEN:
            # api_key = text[3:].strip()
            # setup_token(user_id, api_key)
            api_key = text
            setup_token(user_id, api_key)
            msg = TextSendMessage(text=f'トークンを入力しました。\n{api_key}')
        elif cmd == OpenAIModelCmd.SET_SYSTEM_PROMPT:
            system_prompt = text
            memory.change_system_message(user_id, system_message=system_prompt)
            msg = TextSendMessage(text=f'システムメッセージを変更しました:\n{system_prompt}')
        elif cmd == OpenAIModelCmd.SET_IMAGE_PROMPT:
            prompt = text
            logger.info(f"image {text}")
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
        elif cmd == OpenAIModelCmd.SET_SUMMARIZE_URL:
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
                msg = TextSendMessage(text="入力された内容はURLではありませんでした。")
            memory.append(user_id, role, response)
        elif text.startswith('/token'):
            get_model(user_id).set_command(OpenAIModelCmd.SET_TOKEN)
            # api_key = text[3:].strip()
            # setup_token(user_id, api_key)

            quick_reply_menu = {"キャンセル":"/cancel"}
            items = [QuickReplyButton(action=MessageAction(label=v, text=quick_reply_menu[v])) for k,v in enumerate(quick_reply_menu)]
            msg = TextSendMessage(text='トークンを入力してください。', quick_reply=QuickReply(items=items))
        elif text.startswith('/reset_system_message'):
            system_prompt = text
            memory.change_system_message(user_id, system_message=os.getenv('SYSTEM_MESSAGE'))
            msg = TextSendMessage(text=f'システムメッセージを初期状態に戻しました。')
        elif text in ['ヘルプ', '使い方']:
            quick_reply_menu = {"画像生成":"/image", "URLを要約": "/url", "システムメッセージ":"/system", "システムメッセージをリセット":"/reset_system_message", "履歴をクリア":"/clear", "トークンを入力":"/token"}

            items = [QuickReplyButton(action=MessageAction(label=v, text=quick_reply_menu[v])) for k,v in enumerate(quick_reply_menu)]
            
            msg = TextSendMessage(text="何をしましょうか。",
                                    quick_reply=QuickReply(items=items))
        elif text.startswith('/help'):
            text = '''
            このままチャットすればChatGPTをお手軽に使えます✨
            以下のコマンドも使えます。

            /image 画像の生成をします。
            /url 指定したURLを要約します。
            /system システムメッセージを入力します。例：あなたは有能な弁護士です。
            /reset_system_message システムメッセージを初期状態に戻します。
            /clear ２つ前までのチャット履歴を覚えてますが、その履歴をクリアします。
            /token カスタムのAPI Tokenを入力します。https://platform.openai.com/ に登録すれば取得できます。
            '''[1:-1]
            msg = TextSendMessage(text=textwrap.dedent(text))

        elif text.startswith('/system'):
            get_model(user_id).set_command(OpenAIModelCmd.SET_SYSTEM_PROMPT)
            msg = TextSendMessage(text='システムメッセージを入力してください。\n\n例1 あなたは有能な弁護士です。\n例2 あなたは優秀な小学生の家庭教師です。')
            # memory.change_system_message(user_id, text[5:].strip())
            # msg = TextSendMessage(text='システムプロンプトを入力しました。')

        elif text.startswith('/clear'):
            memory.remove(user_id)
            msg = TextSendMessage(text='履歴をクリアしました。')

        elif text.startswith('/image'):
            get_model(user_id).set_command(OpenAIModelCmd.SET_IMAGE_PROMPT)
            quick_reply_menu = {
                "キャンセル":"/cancel",
                "椅子の上に寝ている可愛い三毛猫の赤ちゃん": None,
                "ドラゴンがバラの花を持っている画像": None,
                "巨大なクマがハンバーガーを食べている画像": None,
                "カエルが宇宙服を着て月面でジャンプしている画像": None,
                "本が自分自身を読んでいる画像": None,
                "オフィスにいる女性が、机に置かれた植物を眺めている画像": None,
                }
            items = [QuickReplyButton(action=MessageAction(label=v, text=quick_reply_menu.get(v) or v) for k,v in enumerate(quick_reply_menu)]
            msg = TextSendMessage(text='どんな画像を生成しますか？', quick_reply=QuickReply(items=items))

            # prompt = text[3:].strip()
            # memory.append(user_id, 'user', prompt)
            # is_successful, response, error_message = get_model(user_id).image_generations(prompt)
            # if not is_successful:
            #     raise Exception(error_message)
            # url = response['data'][0]['url']
            # msg = ImageSendMessage(
            #     original_content_url=url,
            #     preview_image_url=url
            # )
            # memory.append(user_id, 'assistant', url)
        elif text.startswith('/url'):
            get_model(user_id).set_command(OpenAIModelCmd.SET_SUMMARIZE_URL)
            msg = TextSendMessage(text='要約するURLを入力してください。')
        else:
            user_model = get_model(user_id)

            def wrap_msg(msg):
                text=("""
                # 命令書：
                あなたは、ChatGPTです。
                以下の制約条件をもとに、返信および、それに対する質問者の返信例を出力してください。

                # 制約条件：
                ・文字数は300字程度
                ・小学生にもわかりやすく
                ・返信例は最大4つ。それぞれ20字以内
                ・重要なキーワードを取り残さない
                ・文章を簡潔に
                # 入力分:
                """ + msg +
                """
                # 出力文：
                {"reply":"...","reply sample1":"...", ...}
                """)[1:-1]
                text = textwrap.dedent(text)
                return text

            memory.append(user_id, 'user', text)
            ret = memory.get(user_id)
            comp = copy.deepcopy(ret)
            last = comp.pop()
            last["content"]=wrap_msg(last["content"])
            comp.append(last)
            logger.info("送信ログ:\n" + json.dumps(comp))
            is_successful, response, error_message = user_model.chat_completions(comp, os.getenv('OPENAI_MODEL_ENGINE'))
            if not is_successful:
                raise Exception(error_message)
            role, response = get_role_and_content(response)
            logger.info("受信:\n" + response)
            reply, samples = get_reply_and_reply_samples(response)
            logger.info(f"{reply} {samples}")
            items = [QuickReplyButton(action=MessageAction(label=((s[:15]+"..") if len(s)>15 else s), text=s)) for s in samples]
            if len(items)>0:
                msg = TextSendMessage(text=reply, quick_reply=QuickReply(items=items))
            else:
                msg = TextSendMessage(text=reply)
            memory.append(user_id, role, reply)
    except ValueError as e:
        msg = TextSendMessage(text=f'Token が無効です。以下のフォーマットで入力してください。 /token sk-xxxxx {str(e)}')
    except KeyError as e:
        msg = TextSendMessage(text=f'トークンを先に登録してください。/token sk-xxxxx の形式で登録してください。{str(e)}')
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
    # audio_content = line_bot_api.get_message_content(event.message.id)
    # input_audio_path = f'{str(uuid.uuid4())}.m4a'
    # with open(input_audio_path, 'wb') as fd:
    #     for chunk in audio_content.iter_content():
    #         fd.write(chunk)

    # try:
    #     is_successful, response, error_message = get_model(user_id).audio_transcriptions(input_audio_path, 'whisper-1')
    #     if not is_successful:
    #         raise Exception(error_message)
    #     memory.append(user_id, 'user', response['text'])
    #     is_successful, response, error_message = get_model(user_id).chat_completions(memory.get(user_id), 'gpt-3.5-turbo')
    #     if not is_successful:
    #         raise Exception(error_message)
    #     role, response = get_role_and_content(response)
    #     memory.append(user_id, role, response)
    #     msg = TextSendMessage(text=response)
    # except ValueError:
    #     msg = TextSendMessage(text='最初に /token sk-xxxxx の形式でトークンを登録してください。')
    # except KeyError:
    #     msg = TextSendMessage(text='最初に /token sk-xxxxx の形式でトークンを登録してください。')
    # except Exception as e:
    #     memory.remove(user_id)
    #     if str(e).startswith('Incorrect API key provided'):
    #         msg = TextSendMessage(text='OpenAI API Token が正しくありません。/token sk-xxxxx の形式で登録してください。')
    #     else:
    #         msg = TextSendMessage(text=str(e))
    # os.remove(input_audio_path)
    msg = TextSendMessage(text="音声メッセージには対応していません。")
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
    host = '0.0.0.0'
    port = "8080"
    # app.run(host='0.0.0.0', port=8080)
    logger.info(f"start listening: {host}:{port}")
    serve(app, host=host, port=port)