import os
import re
import openai
import PyPDF2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# 配置 OpenAI API
openai.api_key = 'sk-111111111111111111111111111'  # 请替换为你的deepseek API密钥，或者本地apikey
openai.api_base = 'https://api.deepseek.com/beta'  # DeepSeek API的beta地址或者本地apikey
model_name = 'deepseek-coder'   #模型名字
temperature = 0.7
top_p = 0.9

# 添加线程数配置和打印锁
# 并发执行的线程数，deepseek 可以设置为40，其他模型一般为1-5
MAX_WORKERS = 40  # 可以根据需要修改线程数  deepseek 可以设置为40，其他模型一般为1-5
print_lock = Lock()

# 添加重命名开关配置
RENAME_ALL_PDFS = True  # 默认关闭重命名所有PDF的功能  True 为重命名当前文件夹下不包含子文件夹的所有PDF  False 为只重命名翻译后的PDF

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
        prompt=f"{content}\n请用十五个汉字概括本文，如果文本含有有作者创造或改进的或者基于的专有技术体系、框架，则体现出这个专业名词（一般为英文），概括的文本不要含有特殊符号和换行，不要超过十五个汉字长度。只概括，不要做任何其他回答和解释",
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
    if RENAME_ALL_PDFS:
        # 对于所有PDF重命名的情况
        timestamp_pattern = r'(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-).*'
        match = re.search(timestamp_pattern, base_name)
        if match:
            timestamp = match.group(1)  # 直接提取完整的时间戳部分，包含最后的'-'
            new_file_name = f"{timestamp}{new_name}{ext}"
            os.rename(file_path, new_file_name)
            safe_print(f"Renamed {file_path} to {new_file_name}")
        else:
            safe_print(f"Skipped {file_path} as it does not match the required pattern.")
    else:
        # 保持原有的重命名逻辑
        timestamp_pattern = r'\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-'
        match = re.search(timestamp_pattern, base_name)
        if match:
            timestamp = match.group(0)
            new_file_name = f"{timestamp}{new_name}{ext}"
            os.rename(file_path, new_file_name)
            safe_print(f"Renamed {file_path} to {new_file_name}")
        else:
            safe_print(f"Skipped {file_path} as it does not match the required pattern.")

def delete_comparison_pdfs():
    current_directory = os.getcwd()
    for filename in os.listdir(current_directory):
        if filename.endswith('-comparison.pdf'):
            file_path = os.path.join(current_directory, filename)
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file {file_path}: {e}")

def safe_print(*args, **kwargs):
    """线程安全的打印函数"""
    with print_lock:
        print(*args, **kwargs)

def process_single_pdf(file_path):
    """处理单个PDF文件的函数"""
    safe_print("------>：")
    safe_print(file_path)
    try:
        content = read_pdf_content(file_path)
        summary = summarize_content(content)
        rename_pdf_file(file_path, summary)
    except Exception as e:
        safe_print(f"Error processing file {file_path}: {e}")

def process_pdf_files(directory):
    pdf_files = []
    for filename in os.listdir(directory):
        if RENAME_ALL_PDFS:
            # 当开关打开时，处理所有PDF文件
            if filename.endswith('.pdf'):
                file_path = os.path.join(directory, filename)
                pdf_files.append(file_path)
        else:
            # 保持原有逻辑，只处理翻译后的PDF文件
            if filename.endswith('.pdf') and ('-merge_translate_zh.pdf' in filename or '-translate_zh.pdf' in filename):
                file_path = os.path.join(directory, filename)
                pdf_files.append(file_path)
    
    # 使用线程池处理文件
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.map(process_single_pdf, pdf_files)

if __name__ == "__main__":
    delete_comparison_pdfs()
    directory = '.'
    process_pdf_files(directory)
    input("程序执行完毕，按回车键关闭窗口...") 