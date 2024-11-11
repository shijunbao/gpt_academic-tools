import os
import re
import openai
import PyPDF2
from datetime import datetime

# 配置 OpenAI API
openai.api_key = 'sk-111111111111111111111111111'  # 请替换为你的deepseek API密钥，或者本地apikey
openai.api_base = 'https://api.deepseek.com/beta'  # DeepSeek API的beta地址或者本地apikey
model_name = 'deepseek-coder'   #模型名字
temperature = 0.7
top_p = 0.9

# 读取第一页 第二页  返回读取的文本信息
def read_pdf_content(file_path):
    with open(file_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        first_page = reader.pages[0]
        first_page_text = first_page.extract_text()
        if len(first_page_text) < 200:
            second_page = reader.pages[1]
            second_page_text = second_page.extract_text()
            combined_text = first_page_text + second_page_text
            return combined_text
        else:
            return first_page_text

def summarize_content(content):
    response = openai.Completion.create(
        model=model_name,
        prompt=f"{content}\n请用十五个汉字概括本文，可以包含少量英语专业名词，不要含有特殊符号和换行，不要超过十五个汉字长度。只概括，不要做任何其他回答和解释",
        max_tokens=20,
        temperature=temperature,
        top_p=top_p
    )
    summary = response.choices[0].text.strip()
    summary = re.sub(r'[^\w\s]', '', summary)
    summary = summary.replace(' ', '_')
    lines = summary.split('\n')
    if len(lines) > 0:
        if len(lines[0]) > 10:
            summary = lines[0]
        elif len(lines) > 1 and len(lines[0]) < 10 and len(lines[1]) > 10:
            summary = lines[0] + '_' + lines[1]
        elif len(lines) > 2 and len(lines[0]) < 10 and len(lines[1]) < 10 and len(lines[2]) > 10:
            summary = lines[0] + '_' + lines[1] + '_' + lines[2]
        else:
            summary = '_'.join(lines[:4])
    chinese_char_count = 0
    letter_count = 0
    for char in summary:
        if '\u4e00' <= char <= '\u9fff':
            chinese_char_count += 1
        elif char.isalpha():
            letter_count += 1
        if chinese_char_count + letter_count > 15:
            summary = summary[:30]
            break
    print(summary)
    return summary

def rename_pdf_file(file_path, new_name):
    base_name, ext = os.path.splitext(file_path)
    timestamp_pattern = r'\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-'
    match = re.search(timestamp_pattern, base_name)
    if match:
        timestamp = match.group(0)
        new_file_name = f"{timestamp}{new_name}{ext}"
        os.rename(file_path, new_file_name)
        print(f"Renamed {file_path} to {new_file_name}")
    else:
        print(f"Skipped {file_path} as it does not match the required pattern.")

def delete_comparison_pdfs():
    current_directory = os.getcwd()
    for filename in os.listdir(current_directory):
        if filename.endswith('-comparison.pdf'):
            file_path = os.path.join(current_directory, filename)
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file {file_path}: {e}")

def process_pdf_files(directory):
    for filename in os.listdir(directory):
        if filename.endswith('.pdf') and ('-merge_translate_zh.pdf' in filename or '-translate_zh.pdf' in filename):
            file_path = os.path.join(directory, filename)
            print("------>：")
            print(file_path)
            try:
                content = read_pdf_content(file_path)
            except Exception as e:
                print(f"Error reading PDF file {file_path}: {e}")
                continue
            try:
                summary = summarize_content(content)
            except Exception as e:
                print(f"Error summarizing content for file {file_path}: {e}")
                continue
            try:
                rename_pdf_file(file_path, summary)
            except Exception as e:
                print(f"Error renaming PDF file {file_path}: {e}")
                continue

if __name__ == "__main__":
    delete_comparison_pdfs()
    directory = '.'
    process_pdf_files(directory)
    input("程序执行完毕，按回车键关闭窗口...") 