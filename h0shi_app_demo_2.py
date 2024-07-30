import os 
import json 
import random
import time
import requests
from datetime import datetime
import streamlit as st
import openai
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import re
import pandas as pd
import logging
import sqlite3


logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s')
logging.getLogger().addHandler(logging.NullHandler())

openai.api_key = st.secrets["OPENAI_API_KEY"]

USER_DATA_FILE = "user_data.json"

# データベースの初期化
conn = sqlite3.connect('user_data.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                openai_api_key TEXT,
                slack_api_key TEXT
            )''')

def load_user(username):
    c.execute('SELECT * FROM users WHERE username=?', (username,))
    return c.fetchone()

def save_user(username, password, openai_api_key=None, slack_api_key=None):
    c.execute('INSERT OR REPLACE INTO users (username, password, openai_api_key, slack_api_key) VALUES (?, ?, ?, ?)',
              (username, password, openai_api_key, slack_api_key))
    conn.commit()


st.title('模倣ボット')

# 状態管理の初期化
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'tokens_users_set' not in st.session_state:
    st.session_state.tokens_users_set = False
if 'data_prepared' not in st.session_state:
    st.session_state.data_prepared = False
if 'fine_tuning_started' not in st.session_state:
    st.session_state.fine_tuning_started = False
if 'all_succeeded' not in st.session_state:
    st.session_state.all_succeeded = False
if 'finetuning_ids' not in st.session_state:
    st.session_state.finetuning_ids = []
if 'user_names' not in st.session_state:
    st.session_state.user_names = []
if 'model_names' not in st.session_state:
    st.session_state.model_names = []


if not st.session_state.logged_in:
    login_option = st.selectbox("Choose an option", ["Login", "Register"])
    
    if login_option == "Login":
        username = st.text_input("Username")
        password = st.text_input("Password", type='password')
        if st.button('Login'):
            user = load_user(username)
            if user and user[1] == password:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.session_state.openai_api_key = user[2]
                st.session_state.slack_api_key = user[3]
                st.success("Login successful")
                st.session_state.existing_models = get_existing_models(username)
                st.rerun()
            else:
                st.error("Invalid username or password")
    else:
        new_username = st.text_input("New Username")
        new_password = st.text_input("New Password", type='password')
        if st.button('Register'):
            if load_user(new_username):
                st.error("Username already exists")
            else:
                save_user(new_username, new_password)
                st.success("User registered successfully. Please login.")

else:
    existing_models = get_existing_models(st.session_state.username)
    if existing_models:
        st.success(f"すでに作成したモデルを使う{st.session_state.username}")
        
        st.header('ファインチューニングモデルとチャット')
        selected_model = st.selectbox('チャットするモデルを選んでください', existing_models)

        user_input = st.text_input("ここに書き込んでください")
        if st.button('Submit'):
                openai.api_key = os.getenv("OPENAI_API_KEY")
                response = openai.chat.completions.create(
                    model=selected_model,
                    messages=[
                        {"role": "system", "content": f"あなたは{selected_model}です"},
                        {"role": "user", "content": user_input}
                    ],
                    max_tokens=150
                )
            
                generated_response = response.choices[0].message.content.strip()
                st.write(f"返答: {generated_response}")

    else:
        st.header('Information Input')
        user_token = st.text_input("User OAuth Token", type="password")
        bot_token = st.text_input("Bot OAuth Token", type="password")
        user_name_1 = st.text_input("User Name 1")
        user_name_2 = st.text_input("User Name 2")
        user_name_3 = st.text_input("User Name 3")
        my_user_name = st.text_input("My Username")

        if st.button('Set Tokens and Users'):
            st.session_state.user_token = user_token
            st.session_state.bot_token = bot_token
            st.session_state.user_name_1 = user_name_1
            st.session_state.user_name_2 = user_name_2
            st.session_state.user_name_3 = user_name_3
            st.session_state.my_user_name = my_user_name
            st.session_state.tokens_users_set = True

        if st.session_state.tokens_users_set:
            st.success("トークンとユーザーがセットされました！")

        if st.session_state.data_prepared:
            st.success("Data prepared for fine-tuning")

        if st.session_state.fine_tuning_started:
            st.success("Fine-tuning started")

        if st.button('Start Fine-Tuning'):
            logging.info("ファインチューニング開始")
            headers = {'Authorization': f'Bearer {st.session_state.bot_token}'}
            client = WebClient(token=st.session_state.user_token)

            def get_user_id(user_name):
                logging.info(f"Fetching user ID for {user_name}...")
                try:
                    while True:
                        response = requests.get('https://slack.com/api/users.list', headers=headers)
                        if response.status_code == 429:
                            retry_after = int(response.headers.get('Retry-After', 1))
                            time.sleep(retry_after)
                        else:
                            response.raise_for_status()
                            users = response.json().get('members', [])
                            for user in users:
                                profile = user.get('profile', {})
                                if profile.get('display_name') == user_name:
                                    return user['id']
                            break
                except requests.exceptions.RequestException as e:
                    st.error(f"Error fetching user list: {e}")
                return None

            user_names = [st.session_state.user_name_1, st.session_state.user_name_2, st.session_state.user_name_3]
            user_ids = [get_user_id(st.session_state.user_name_1), get_user_id(st.session_state.user_name_2), get_user_id(st.session_state.user_name_3)]
            my_id = get_user_id(st.session_state.my_user_name)
            logging.info(f"User IDs: {user_ids}")

            def fetch_all_messages(user_id):
                logging.info(f"Fetching all messages for user ID {user_id}...")
                try:
                    response = client.conversations_open(users=[user_id])
                    if response['ok']:
                        channel_id = response['channel']['id']
                        messages = []
                        has_more = True
                        latest = datetime.now().timestamp()
                        while has_more and len(messages) < 500:
                            response = client.conversations_history(channel=channel_id, latest=str(latest), limit=1000)
                            if response['ok']:
                                messages.extend(response['messages'])
                                has_more = response['has_more']
                                if has_more:
                                    latest = response['messages'][-1]['ts']
                            else:
                                has_more = False
                        logging.info(f"Fetched {len(messages)} messages for user ID {user_id}.")
                        return messages[:500]
                    else:
                        return None
                except SlackApiError as e:
                    st.error(f"An error occurred: {e.response['error']}")
                    return None

            def format_messages_to_jsonl(messages, model_user_name, model_id, my_id, filename):
                data = []
                system_message = {"role": "system", "content": f"あなたは{model_user_name}です"}
                user_message = None
                assistant_message = None
                for message in messages:
                    text = message.get("text")
                    content = re.sub(r'<@[^>]+>', '', text)
                    user = message.get("user")
                    if user == model_id:
                        assistant_message = {"role": "assistant", "content": content}
                    elif user == my_id:
                        user_message = {"role": "user", "content": content}
                    if user_message and assistant_message:
                        combined_messages = [system_message, user_message, assistant_message]
                        data.append({"messages": combined_messages})
                        user_message = None
                        assistant_message = None
                return data
            
            training_files = {}
            for user_name, user_id in zip(user_names, user_ids):
                messages = fetch_all_messages(user_id)
                if messages:
                    data = format_messages_to_jsonl(messages, user_name, user_id, my_id, None)
                    training_files[user_name] = data
                else:
                    st.error(f"No messages found for user {user_name}")
            st.session_state.training_files = training_files
            st.session_state.data_prepared = True
            st.session_state.user_names = user_names
            st.session_state.user_ids = user_ids
            st.rerun()

            
        if st.session_state.data_prepared and not st.session_state.fine_tuning_started:
            def load_and_shuffle_data(file_path):
                if not os.path.isfile(file_path):
                    st.error(f"File not found: {file_path}")
                    return None
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = [json.loads(line.strip()) for line in file.readlines()]
                random.shuffle(data)
                logging.info(f"Data loaded and shuffled from {file_path}.")
                return data

            def split_data(data, train_ratio=0.9):
                split_idx = int(len(data) * train_ratio)
                train_data = data[:split_idx]
                test_data = data[split_idx:]
                logging.info(f"Data split into {len(train_data)} training samples and {len(test_data)} test samples.")
                return train_data, test_data

            def save_data_to_file(data, file_path):
                with open(file_path, 'w', encoding='utf-8') as f:
                    for item in data:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
                logging.info(f"Data saved to {file_path}.")

            def fine_tune_model(train_data, test_data, api_token):
                openai.api_key = api_token
                train_file = openai.files.create(file=open(train_data, "rb"), purpose="fine-tune")
                validation_file = openai.files.create(file=open(test_data, "rb"), purpose="fine-tune")
                response = openai.fine_tuning.jobs.create(model="gpt-3.5-turbo", training_file=train_file.id, validation_file=validation_file.id, hyperparameters={"n_epochs": 3})
                logging.info(f"Fine-tuning started with job ID: {response.id}")
                return response.id
            finetuning_ids = []
            for user_name, data in st.session_state.training_files.items():
                train_data, test_data = split_data(data)
                train_file_path = f"/tmp/train_data_{user_name}.jsonl"
                test_file_path = f"/tmp/test_data_{user_name}.jsonl"
                save_data_to_file(train_data, train_file_path)
                save_data_to_file(test_data, test_file_path)
                finetuning_id = fine_tune_model(train_file_path, test_file_path, openai.api_key)
                finetuning_ids.append(finetuning_id)

            st.session_state.finetuning_ids = finetuning_ids
            st.session_state.fine_tuning_started = True
            st.rerun()

        if st.session_state.fine_tuning_started and not st.session_state.all_succeeded:
            headers = {
                "Authorization": f"Bearer {openai.api_key}",
                "Content-Type": "application/json"
            }

            def check_fine_tuning_status(finetuning_id):
                try:
                    response = requests.get(f"https://api.openai.com/v1/fine_tuning/jobs/{finetuning_id}", headers=headers)
                    response.raise_for_status()
                    return response.json()["status"]
                except requests.exceptions.RequestException as e:
                    st.error(f"Error checking fine-tuning status: {e}")
                    return None
                
            while True:
                all_succeeded = True
                for finetuning_id in st.session_state.finetuning_ids:
                    status = check_fine_tuning_status(finetuning_id)
                    if status != "succeeded":
                        all_succeeded = False
                        break

                if all_succeeded:
                    model_names = []
                    for finetuning_id in st.session_state.finetuning_ids:
                        response = openai.fine_tuning.jobs.retrieve(finetuning_id)
                        model_name = response.fine_tuned_model
                        model_names.append(model_name)
                    st.session_state.model_names = model_names
                    st.session_state.all_succeeded = True
                    st.success("Fine-tuning succeeded!")
                    st.rerun()

                time.sleep(60)

        if st.session_state.finetuning_ids and st.session_state.all_succeeded:
            st.success("Fine-tuning succeeded!")
            selected_user = st.selectbox('Select a user to chat with', st.session_state.user_names)

            if selected_user:
                user_index = st.session_state.user_names.index(selected_user)
                model_name = st.session_state.model_names[user_index]

                # チャットページ
                st.header('Chat with Fine-Tuned Model')

                user_input = st.text_input("Enter your question:")
            if st.button('Submit'):
                response = openai.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": f"あなたは{selected_user}です"},
                        {"role": "user", "content": user_input}
                    ],
                    max_tokens=150
                )
                generated_response = response.choices[0].message.content.strip()
                st.write(f"Response: {generated_response}")

        
            









