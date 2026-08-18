import math
import os
from datetime import datetime
from flask import Flask, abort, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    DatetimePickerAction,
    MessageEvent,
    PostbackEvent,
    TemplateSendMessage,
    TextMessage,
    TextSendMessage,
)
from linebot.models.template import ButtonsTemplate

app = Flask(__name__)

# 從環境變數讀取金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 紀錄使用者選擇的時間狀態
user_sessions = {}


def calculate_shifts(hours):
  """8664 規則 + 0.5 半班進位計算"""
  if hours <= 0:
    return 0
  elif hours <= 8:
    return 1
  elif hours <= 11:
    return 1.5
  elif hours <= 14:
    return 2
  elif hours <= 17:
    return 2.5
  elif hours <= 20:
    return 3
  elif hours <= 22:
    return 3.5
  elif hours <= 24:
    return 4
  else:
    extra_hours = hours - 24
    return 4 + (math.ceil(extra_hours / 4 / 0.5) * 0.5)


@app.route('/callback', methods=['POST'])
def callback():
  signature = request.headers.get('X-Line-Signature')
  body = request.get_data(as_text=True)
  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return 'OK'


def send_time_picker(reply_token, text_title):
  buttons_template = ButtonsTemplate(
      title='🎬 影視班費計算器',
      text=text_title,
      actions=[
          DatetimePickerAction(
              label='1. 選擇【開始時間】',
              data='action=set_start',
              mode='datetime',
          ),
          DatetimePickerAction(
              label='2. 選擇【結束時間】',
              data='action=set_end',
              mode='datetime',
          ),
      ],
  )
  line_bot_api.reply_message(
      reply_token,
      TemplateSendMessage(
          alt_text='請選擇拍攝時間', template=buttons_template
      ),
  )


# 使用者傳送任何文字訊息，清空舊紀錄並發送選單
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
  user_id = event.source.user_id
  # 重新觸發時重置舊資料
  user_sessions[user_id] = {}
  send_time_picker(event.reply_token, '請點擊下方按鈕選擇拍攝時間：')


# 處理時間選擇事件
@handler.add(PostbackEvent)
def handle_postback(event):
  user_id = event.source.user_id
  postback_data = event.postback.data
  selected_params = event.postback.params

  # 確保使用者 Session 存在
  if user_id not in user_sessions:
    user_sessions[user_id] = {}

  if 'action=set_start' in postback_data:
    start_str = selected_params['datetime'].replace('T', ' ')
    user_sessions[user_id]['start'] = start_str

    # 若選了開始時間但還沒選結束時間（或剛重置），提示選擇結束時間
    if 'end' not in user_sessions[user_id]:
      line_bot_api.reply_message(
          event.reply_token,
          TextSendMessage(
              text=f'✅ 已設定開始時間：{start_str}\n\n請繼續點選『2.'
              ' 選擇【結束時間】』按鈕。'
          ),
      )
    else:
      process_calc(
          event.reply_token,
          user_sessions[user_id]['start'],
          user_sessions[user_id]['end'],
          user_id,
      )

  elif 'action=set_end' in postback_data:
    end_str = selected_params['datetime'].replace('T', ' ')
    user_sessions[user_id]['end'] = end_str

    if 'start' not in user_sessions[user_id]:
      line_bot_api.reply_message(
          event.reply_token,
          TextSendMessage(
              text=f'✅ 已設定結束時間：{end_str}\n\n請繼續點選『1.'
              ' 選擇【開始時間】』按鈕。'
          ),
      )
    else:
      process_calc(
          event.reply_token,
          user_sessions[user_id]['start'],
          user_sessions[user_id]['end'],
          user_id,
      )


def process_calc(reply_token, start_str, end_str, user_id):
  fmt = '%Y-%m-%d %H:%M'
  duration = (
      datetime.strptime(end_str, fmt) - datetime.strptime(start_str, fmt)
  ).total_seconds() / 3600

  # 防呆判斷：結束時間早於開始時間
  if duration < 0:
    reply_text = (
        f'⚠️ 時間設定有誤！\n結束時間 ({end_str}) 不能早於開始時間 ({start_str})。\n請重新點選按鈕選擇時間！'
    )
  else:
    shifts = calculate_shifts(duration)
    reply_text = (
        f'🎬 影視班費計算結果：\n-------------------\n🛫 開始：{start_str}\n🛬'
        f' 結束：{end_str}\n⏱ 總時長：{duration:.2f} 小時\n📊 結算班數：{shifts}'
        ' 班\n\n💡 傳送任意訊息即可再次計算！'
    )

  # 計算完後清空該使用者的暫存紀錄，方便下次乾淨計算
  user_sessions[user_id] = {}
  line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))


if __name__ == '__main__':
  port = int(os.environ.get('PORT', 5000))
  app.run(host='0.0.0.0', port=port)
