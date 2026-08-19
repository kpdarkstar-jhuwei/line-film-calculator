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

# 紀錄使用者選擇的時間與設定狀態 (格式: { user_id: {'start': ..., 'end': ..., 'daily_rate': ..., 'awaiting_rate': True/False } })
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


def send_main_menu(reply_token, user_id, text_title):
  """發送主選單 (含時間選擇與日薪設定)"""
  session = user_sessions.get(user_id, {})
  current_rate = session.get('daily_rate', None)
  rate_info = (
      f' (目前日薪: ${current_rate:,})' if current_rate is not None else ' (未設定)'
  )

  buttons_template = ButtonsTemplate(
      title='🎬 影視班費計算器',
      text=f'{text_title}\n{rate_info}',
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
          {
              'type': 'postback',
              'label': '💰 設定/修改日薪',
              'data': 'action=set_rate',
          },
      ],
  )
  line_bot_api.reply_message(
      reply_token,
      TemplateSendMessage(
          alt_text='請選擇拍攝時間或設定日薪', template=buttons_template
      ),
  )


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
  user_id = event.source.user_id
  user_text = event.message.text.strip()

  if user_id not in user_sessions:
    user_sessions[user_id] = {}

  session = user_sessions[user_id]

  # 判斷使用者是否正在輸入日薪數字
  if session.get('awaiting_rate'):
    if user_text.isdigit() and int(user_text) > 0:
      rate = int(user_text)
      session['daily_rate'] = rate
      session['awaiting_rate'] = False
      line_bot_api.reply_message(
          event.reply_token,
          TextSendMessage(
              text=f'✅ 已成功設定日薪為：${rate:,} 元！\n\n請點擊下方按鈕選擇拍攝時間：'
          ),
      )
      send_main_menu(
          event.reply_token,
          user_id,
          f'✅ 已成功設定日薪為：${rate:,} 元！\n請選擇拍攝時間：',
      )
    else:
      line_bot_api.reply_message(
          event.reply_token,
          TextSendMessage(
              text='⚠️ 請輸入正確的純數字（例如：3000），請重新輸入日薪：'
          ),
      )
    return

  # 普通文字訊息：重置時間狀態並顯示選單
  session['start'] = None
  session['end'] = None
  user_sessions[user_id] = session
  send_main_menu(
      event.reply_token, user_id, '請點擊下方按鈕選擇拍攝時間或設定日薪：'
  )


@handler.add(PostbackEvent)
def handle_postback(event):
  user_id = event.source.user_id
  postback_data = event.postback.data

  if user_id not in user_sessions:
    user_sessions[user_id] = {}

  session = user_sessions[user_id]

  # 點擊「設定日薪」
  if 'action=set_rate' in postback_data:
    session['awaiting_rate'] = True
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text=(
                '💰 請在對話框直接回覆您的【單日班費/日薪】數字\n例如輸入：3000'
                ' 或 4500'
            )
        ),
    )
    return

  selected_params = event.postback.params

  if 'action=set_start' in postback_data:
    start_str = selected_params['datetime'].replace('T', ' ')
    session['start'] = start_str

    if not session.get('end'):
      line_bot_api.reply_message(
          event.reply_token,
          TextSendMessage(
              text=f'✅ 已設定開始時間：{start_str}\n\n請繼續點選『2.'
              ' 選擇【結束時間】』按鈕。'
          ),
      )
    else:
      process_calc(
          event.reply_token, session['start'], session['end'], user_id
      )

  elif 'action=set_end' in postback_data:
    end_str = selected_params['datetime'].replace('T', ' ')
    session['end'] = end_str

    if not session.get('start'):
      line_bot_api.reply_message(
          event.reply_token,
          TextSendMessage(
              text=f'✅ 已設定結束時間：{end_str}\n\n請繼續點選『1.'
              ' 選擇【開始時間】』按鈕。'
          ),
      )
    else:
      process_calc(
          event.reply_token, session['start'], session['end'], user_id
      )


def process_calc(reply_token, start_str, end_str, user_id):
  fmt = '%Y-%m-%d %H:%M'
  duration = (
      datetime.strptime(end_str, fmt) - datetime.strptime(start_str, fmt)
  ).total_seconds() / 3600

  session = user_sessions.get(user_id, {})
  daily_rate = session.get('daily_rate', None)

  if duration < 0:
    reply_text = (
        f'⚠️ 時間設定有誤！\n結束時間 ({end_str}) 不能早於開始時間 ({start_str})。\n請重新點選按鈕選擇時間！'
    )
  else:
    shifts = calculate_shifts(duration)

    reply_text = (
        f'🎬 影視班費計算結果：\n-------------------\n🛫 開始：{start_str}\n🛬'
        f' 結束：{end_str}\n⏱ 總時長：{duration:.2f} 小時\n📊 結算班數：{shifts}'
        ' 班\n'
    )

    if daily_rate is not None:
      total_fee = int(shifts * daily_rate)
      reply_text += f'💵 約定日薪：${daily_rate:,} 元\n💰 預估班費：${total_fee:,} 元\n'
    else:
      reply_text += (
          '\n💡 提示：點擊『💰 設定/修改日薪』按鈕，即可自動計算總金額喔！\n'
      )

    reply_text += '\n💡 傳送任意訊息即可再次計算！'

  # 重置時間，方便下次乾淨計算（保留 daily_rate）
  session['start'] = None
  session['end'] = None
  line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))


if __name__ == '__main__':
  port = int(os.environ.get('PORT', 5000))
  app.run(host='0.0.0.0', port=port)
