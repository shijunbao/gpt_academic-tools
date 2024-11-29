from toolbox import update_ui, trimmed_format_exc, get_conf, get_log_folder, promote_file_to_downloadzone, check_repeat_upload, map_file_to_sha256
from toolbox import CatchException, report_exception, update_ui_lastest_msg, zip_result, gen_time_str
from functools import partial
from loguru import logger

import glob, os, requests, time, json, tarfile, threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

class TimeoutException(Exception):
    pass

def run_with_timeout(func, args=(), kwargs={}, timeout_duration=300):
    """使用threading.Timer实现的超时装饰器"""
    result = []
    is_timeout = []
    
    def _target():
        try:
            result.append(func(*args, **kwargs))
        except Exception as e:
            result.append(e)
    
    def _timeout_handler():
        is_timeout.append(True)
        if len(result) == 0:
            result.append(TimeoutException("Operation timed out"))
    
    timer = threading.Timer(timeout_duration, _timeout_handler)
    thread = threading.Thread(target=_target)
    
    timer.start()
    thread.start()
    thread.join()
    timer.cancel()
    
    if result[0] is not None and isinstance(result[0], Exception):
        raise result[0]
    return result[0]

def run_generator_with_timeout(generator, timeout_duration=300):
    """专门处理生成器的超时装饰器"""
    result = []
    is_done = []
    final_result = None
    
    def _run_generator():
        try:
            for item in generator:
                if is_done:  # 如果超时标志被设置，立即退出
                    return
                # 保存所有非None的结果
                if item is not None:
                    result.append(item)
        except Exception as e:
            result.append(e)
            return
    
    thread = threading.Thread(target=_run_generator)
    thread.daemon = True  # 设置为守护线程
    thread.start()
    thread.join(timeout_duration)  # 等待指定时间
    
    if thread.is_alive():  # 如果线程还活着，说明超时了
        is_done.append(True)  # 设置超时标志
        raise TimeoutException("Operation timed out")
    
    if not result:
        raise Exception("No result from download generator")
    
    if isinstance(result[0], Exception):
        raise result[0]
    
    # 获取最后一个结果，并确保它是一个有效的元组
    final_result = result[-1]
    if not isinstance(final_result, tuple) or len(final_result) != 2:
        raise Exception("Invalid result format from download generator")
        
    return final_result

pj = os.path.join
ARXIV_CACHE_DIR = get_conf("ARXIV_CACHE_DIR")


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=- 工具函数 =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
# 专业词汇声明  = 'If the term "agent" is used in this section, it should be translated to "智能体". '
def switch_prompt(pfg, mode, more_requirement):
    """
    Generate prompts and system prompts based on the mode for proofreading or translating.
    Args:
    - pfg: Proofreader or Translator instance.
    - mode: A string specifying the mode, either 'proofread' or 'translate_zh'.

    Returns:
    - inputs_array: A list of strings containing prompts for users to respond to.
    - sys_prompt_array: A list of strings containing prompts for system prompts.
    """
    n_split = len(pfg.sp_file_contents)
    if mode == 'proofread_en':
        inputs_array = [r"Below is a section from an academic paper, proofread this section." +
                        r"Do not modify any latex command such as \section, \cite, \begin, \item and equations. " + more_requirement +
                        r"Answer me only with the revised text:" +
                        f"\n\n{frag}" for frag in pfg.sp_file_contents]
        sys_prompt_array = ["You are a professional academic paper writer." for _ in range(n_split)]
    elif mode == 'translate_zh':
        inputs_array = [
            r"Below is a section from an English academic paper, translate it into Chinese. " + more_requirement +
            r"Do not modify any latex command such as \section, \cite, \begin, \item and equations. " +
            r"Answer me only with the translated text:" +
            f"\n\n{frag}" for frag in pfg.sp_file_contents]
        sys_prompt_array = ["You are a professional translator." for _ in range(n_split)]
    else:
        assert False, "未知指令"
    return inputs_array, sys_prompt_array


def desend_to_extracted_folder_if_exist(project_folder):
    """
    Descend into the extracted folder if it exists, otherwise return the original folder.

    Args:
    - project_folder: A string specifying the folder path.

    Returns:
    - A string specifying the path to the extracted folder, or the original folder if there is no extracted folder.
    """
    maybe_dir = [f for f in glob.glob(f'{project_folder}/*') if os.path.isdir(f)]
    if len(maybe_dir) == 0: return project_folder
    if maybe_dir[0].endswith('.extract'): return maybe_dir[0]
    return project_folder


def move_project(project_folder, arxiv_id=None):
    """
    Create a new work folder and copy the project folder to it.

    Args:
    - project_folder: A string specifying the folder path of the project.

    Returns:
    - A string specifying the path to the new work folder.
    """
    import shutil, time
    time.sleep(2)  # avoid time string conflict
    if arxiv_id is not None:
        new_workfolder = pj(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    else:
        new_workfolder = f'{get_log_folder()}/{gen_time_str()}'
    try:
        shutil.rmtree(new_workfolder)
    except:
        pass

    # align subfolder if there is a folder wrapper
    items = glob.glob(pj(project_folder, '*'))
    items = [item for item in items if os.path.basename(item) != '__MACOSX']
    if len(glob.glob(pj(project_folder, '*.tex'))) == 0 and len(items) == 1:
        if os.path.isdir(items[0]): project_folder = items[0]

    shutil.copytree(src=project_folder, dst=new_workfolder)
    return new_workfolder


def arxiv_download(chatbot, history, txt, allow_cache=True):
    def check_cached_translation_pdf(arxiv_id):
        translation_dir = pj(ARXIV_CACHE_DIR, arxiv_id, 'translation')
        if not os.path.exists(translation_dir):
            os.makedirs(translation_dir)
        target_file = pj(translation_dir, 'translate_zh.pdf')
        if os.path.exists(target_file):
            promote_file_to_downloadzone(target_file, rename_file=None, chatbot=chatbot)
            target_file_compare = pj(translation_dir, 'comparison.pdf')
            if os.path.exists(target_file_compare):
                promote_file_to_downloadzone(target_file_compare, rename_file=None, chatbot=chatbot)
            return target_file
        return False

    def is_float(s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    if txt.startswith('https://arxiv.org/pdf/'):
        arxiv_id = txt.split('/')[-1]   # 2402.14207v2.pdf
        txt = arxiv_id.split('v')[0]  # 2402.14207

    if ('.' in txt) and ('/' not in txt) and is_float(txt):  # is arxiv ID
        txt = 'https://arxiv.org/abs/' + txt.strip()
    if ('.' in txt) and ('/' not in txt) and is_float(txt[:10]):  # is arxiv ID
        txt = 'https://arxiv.org/abs/' + txt[:10]

    if not txt.startswith('https://arxiv.org'):
        return txt, None    # 是本地文件，跳过下载

    # <-------------- inspect format ------------->
    chatbot.append([f"检到arxiv文档连接", '尝试下载 ...'])
    yield from update_ui(chatbot=chatbot, history=history)
    time.sleep(1)  # 刷新界面

    url_ = txt  # https://arxiv.org/abs/1707.06690

    if not txt.startswith('https://arxiv.org/abs/'):
        msg = f"解析arxiv网址失败, 期望格式例如: https://arxiv.org/abs/1707.06690。实际得到格式: {url_}。"
        yield from update_ui_lastest_msg(msg, chatbot=chatbot, history=history)  # 刷新界面
        return msg, None
    # <-------------- set format ------------->
    arxiv_id = url_.split('/abs/')[-1]
    if 'v' in arxiv_id: arxiv_id = arxiv_id[:10]
    cached_translation_pdf = check_cached_translation_pdf(arxiv_id)
    if cached_translation_pdf and allow_cache: return cached_translation_pdf, arxiv_id

    extract_dst = pj(ARXIV_CACHE_DIR, arxiv_id, 'extract')
    translation_dir = pj(ARXIV_CACHE_DIR, arxiv_id, 'e-print')
    dst = pj(translation_dir, arxiv_id + '.tar')
    os.makedirs(translation_dir, exist_ok=True)
    # <-------------- download arxiv source file ------------->

    def fix_url_and_download():
        # for url_tar in [url_.replace('/abs/', '/e-print/'), url_.replace('/abs/', '/src/')]:
        for url_tar in [url_.replace('/abs/', '/src/'), url_.replace('/abs/', '/e-print/')]:
            try:
                proxies = get_conf('proxies')
                r = requests.get(url_tar, proxies=proxies)
                if r.status_code == 200:
                    with open(dst, 'wb+') as f:
                        f.write(r.content)
                    return True
            except (requests.exceptions.ConnectionError, requests.exceptions.RequestException) as e:
                logger.error(f"Download failed for {url_tar}: {str(e)}")
                continue
        return False

    if os.path.exists(dst) and allow_cache:
        yield from update_ui_lastest_msg(f"调用缓存 {arxiv_id}", chatbot=chatbot, history=history)  # 刷新界面
        success = True
    else:
        yield from update_ui_lastest_msg(f"开始下载 {arxiv_id}", chatbot=chatbot, history=history)  # 刷新界面
        max_retries = 1
        success = False
        for retry in range(max_retries):
            try:
                success = fix_url_and_download()
                if success:
                    break
                if retry < max_retries - 1:
                    yield from update_ui_lastest_msg(f"下载失败，正在重试 ({retry + 1}/{max_retries})...", chatbot=chatbot, history=history)
                    time.sleep(5)  # Wait before retry
            except Exception as e:
                logger.error(f"Error during download attempt {retry + 1}: {str(e)}")
                if retry < max_retries - 1:
                    yield from update_ui_lastest_msg(f"下载出错，正在重试 ({retry + 1}/{max_retries})...", chatbot=chatbot, history=history)
                    time.sleep(5)  # Wait before retry
                continue
        yield from update_ui_lastest_msg(f"下载完成 {arxiv_id}" if success else f"下载失败 {arxiv_id}", chatbot=chatbot, history=history)

    if not success:
        yield from update_ui_lastest_msg(f"下载失败 {arxiv_id}", chatbot=chatbot, history=history)
        raise tarfile.ReadError(f"论文下载失败 {arxiv_id}")

    # <-------------- extract file ------------->
    from toolbox import extract_archive
    try:
        extract_archive(file_path=dst, dest_dir=extract_dst)
    except Exception as e:
        logger.error(f"Failed to extract {dst}: {str(e)}")
        raise tarfile.ReadError(f"论文解压失败: {str(e)}")

    return extract_dst, arxiv_id


def pdf2tex_project(pdf_file_path, plugin_kwargs):
    if plugin_kwargs["method"] == "MATHPIX":
        # Mathpix API credentials
        app_id, app_key = get_conf('MATHPIX_APPID', 'MATHPIX_APPKEY')
        headers = {"app_id": app_id, "app_key": app_key}

        # Step 1: Send PDF file for processing
        options = {
            "conversion_formats": {"tex.zip": True},
            "math_inline_delimiters": ["$", "$"],
            "rm_spaces": True
        }

        response = requests.post(url="https://api.mathpix.com/v3/pdf",
                                headers=headers,
                                data={"options_json": json.dumps(options)},
                                files={"file": open(pdf_file_path, "rb")})

        if response.ok:
            pdf_id = response.json()["pdf_id"]
            logger.info(f"PDF processing initiated. PDF ID: {pdf_id}")

            # Step 2: Check processing status
            while True:
                conversion_response = requests.get(f"https://api.mathpix.com/v3/pdf/{pdf_id}", headers=headers)
                conversion_data = conversion_response.json()

                if conversion_data["status"] == "completed":
                    logger.info("PDF processing completed.")
                    break
                elif conversion_data["status"] == "error":
                    logger.info("Error occurred during processing.")
                else:
                    logger.info(f"Processing status: {conversion_data['status']}")
                    time.sleep(5)  # wait for a few seconds before checking again

            # Step 3: Save results to local files
            output_dir = os.path.join(os.path.dirname(pdf_file_path), 'mathpix_output')
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            url = f"https://api.mathpix.com/v3/pdf/{pdf_id}.tex"
            response = requests.get(url, headers=headers)
            file_name_wo_dot = '_'.join(os.path.basename(pdf_file_path).split('.')[:-1])
            output_name = f"{file_name_wo_dot}.tex.zip"
            output_path = os.path.join(output_dir, output_name)
            with open(output_path, "wb") as output_file:
                output_file.write(response.content)
            logger.info(f"tex.zip file saved at: {output_path}")

            import zipfile
            unzip_dir = os.path.join(output_dir, file_name_wo_dot)
            with zipfile.ZipFile(output_path, 'r') as zip_ref:
                zip_ref.extractall(unzip_dir)

            return unzip_dir

        else:
            logger.error(f"Error sending PDF for processing. Status code: {response.status_code}")
            return None
    else:
        from crazy_functions.pdf_fns.parse_pdf_via_doc2x import 解析PDF_DOC2X_转Latex
        unzip_dir = 解析PDF_DOC2X_转Latex(pdf_file_path)
        return unzip_dir


def open_folder(folder_path):
    """Open folder in file explorer"""
    try:
        import platform, subprocess
        if platform.system() == 'Windows':
            os.startfile(folder_path)
        elif platform.system() == 'Darwin':  # macOS
            subprocess.run(['open', folder_path])
        else:  # linux
            subprocess.run(['xdg-open', folder_path])
    except Exception as e:
        logger.error(f"Failed to open folder {folder_path}: {str(e)}")


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-= 插件主程序1 =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=


@CatchException
def Latex英文纠错加PDF对比(txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, user_request):
    # <-------------- information about this plugin ------------->
    chatbot.append(["函数插件功能？",
                    "对整个Latex项目进行纠错, 用latex编译为PDF对修正处做高亮。函数插件贡献者: Binary-Husky注意事项: 目前对机器学习类文献转化效果最好，其他类型文献转化效果未知。仅在Windows系统进行了测试，其他操作系统表现未知。"])
    yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面

    # <-------------- more requirements ------------->
    if ("advanced_arg" in plugin_kwargs) and (plugin_kwargs["advanced_arg"] == ""): plugin_kwargs.pop("advanced_arg")
    more_req = plugin_kwargs.get("advanced_arg", "")
    _switch_prompt_ = partial(switch_prompt, more_requirement=more_req)

    # <-------------- check deps ------------->
    try:
        import glob, os, time, subprocess
        subprocess.Popen(['pdflatex', '-version'])
        from .latex_fns.latex_actions import Latex精细分解与转化, 编译Latex
    except Exception as e:
        chatbot.append([f"解析项目: {txt}",
                        f"尝试执行Latex指令失败。Latex没有安装, 或者不在环境变量PATH中。安装方法https://tug.org/texlive/。报错信息\n\n```\n\n{trimmed_format_exc()}\n\n```\n\n"])
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- clear history and read input ------------->
    history = []
    if os.path.exists(txt):
        project_folder = txt
    else:
        if txt == "": txt = '空空如也的输入栏'
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到本地项目或无权访问: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return
    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.tex', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.tex文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- if is a zip/tar file ------------->
    project_folder = desend_to_extracted_folder_if_exist(project_folder)

    # <-------------- move latex project away from temp folder ------------->
    from shared_utils.fastapi_server import validate_path_safety
    validate_path_safety(project_folder, chatbot.get_user())
    project_folder = move_project(project_folder, arxiv_id=None)

    # <-------------- if merge_translate_zh is already generated, skip gpt req ------------->
    if not os.path.exists(project_folder + '/merge_proofread_en.tex'):
        yield from Latex精细分解与转化(file_manifest, project_folder, llm_kwargs, plugin_kwargs,
                                       chatbot, history, system_prompt, mode='proofread_en',
                                       switch_prompt=_switch_prompt_)

    # <-------------- compile PDF ------------->
    success = yield from 编译Latex(chatbot, history, main_file_original='merge',
                                   main_file_modified='merge_proofread_en',
                                   work_folder_original=project_folder, work_folder_modified=project_folder,
                                   work_folder=project_folder)

    # <-------------- zip PDF ------------->
    zip_res = zip_result(project_folder)
    if success:
        chatbot.append((f"成功啦", '请查收结果（压缩包）...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
    else:
        chatbot.append((f"失败了",
                        '虽然PDF生成失败了, 但请查收结果（压缩包）, 内含已经翻译的Tex文档, 也是可读的, 您可以到Github Issue区, 用该压缩包+Conversation_To_File进行反馈 ...'))
        yield from update_ui(chatbot=chatbot, history=history);
        time.sleep(1)  # 刷新界面
        promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)

    # <-------------- we are done ------------->
    return success


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-= 插件主程序2 =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

def read_previous_translation_reports(report_folder):
    """读取历史翻译报告，返回所有已成功翻译的arxiv ID"""
    successful_translations = set()
    try:
        for report in glob.glob(os.path.join(report_folder, '*-arxiv论文批量翻译报告.md')):
            with open(report, 'r', encoding='utf-8') as f:
                content = f.read()
                # 定位到成功翻译的论文部分
                if '### 成功翻译的论文' in content:
                    success_section = content.split('### 成功翻译的论文')[1].split('###')[0]
                    # 提取arxiv ID
                    for line in success_section.strip().split('\n'):
                        if line.startswith('- '):
                            arxiv_id = line[2:].strip()
                            successful_translations.add(arxiv_id)
    except Exception as e:
        logger.error(f"Error reading translation reports: {str(e)}")
    return successful_translations

@CatchException
def Latex翻译中文并重新编译PDF(txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, user_request):
    # <-------------- information about this plugin ------------->
    chatbot.append([
        "函数插件功能？",
        "对整个Latex项目进行翻译, 生成中文PDF。函数插件贡献者: Binary-Husky。注意事项: 此插件Windows支持最佳，Linux下必须使用Docker安装，详见项目主README.md。目前对机器学习类文献转化效果最好，其他类型文献转化效果未知。"])
    yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面

    # <-------------- handle multiple arxiv IDs ------------->
    arxiv_ids = [id.strip() for id in txt.split(',') if id.strip()]
    if not arxiv_ids:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"未找到有效的arxiv ID")
        yield from update_ui(chatbot=chatbot, history=history)
        return
        
    # <-------------- more requirements ------------->
    if ("advanced_arg" in plugin_kwargs) and (plugin_kwargs["advanced_arg"] == ""): plugin_kwargs.pop("advanced_arg")
    more_req = plugin_kwargs.get("advanced_arg", "")

    no_cache = ("--no-cache" in more_req)
    if no_cache: more_req = more_req.replace("--no-cache", "").strip()

    allow_gptac_cloud_io = ("--allow-cloudio" in more_req)  # 从云端下载翻译结果，以及上传翻译结果到云端
    if allow_gptac_cloud_io: more_req = more_req.replace("--allow-cloudio", "").strip()

    allow_cache = not no_cache
    _switch_prompt_ = partial(switch_prompt, more_requirement=more_req)

    # <-------------- check deps ------------->
    try:
        import glob, os, time, subprocess
        subprocess.Popen(['pdflatex', '-version'])
        from .latex_fns.latex_actions import Latex精细分解与转化, 编译Latex
    except Exception as e:
        chatbot.append([f"解析项目: {txt}",
                        f"尝试执行Latex指令失败。Latex没有安装, 或者不在环境变量PATH中。安装方法https://tug.org/texlive/。报错信息\n\n```\n\n{trimmed_format_exc()}\n\n```\n\n"])
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- prepare report data ------------->
    from datetime import datetime
    task_start_time = datetime.now()
    successful_ids = []
    failed_ids = []
    task_times = []

    # 在开始处生成一个唯一的批次ID
    batch_id = datetime.now().strftime('%y-%m-%d-%H-%M-%S')
    report_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "report")
    os.makedirs(report_folder, exist_ok=True)
    if len(arxiv_ids) > 5: open_folder(report_folder)

    # 读取历史翻译记录
    previously_translated = read_previous_translation_reports(report_folder)
    if previously_translated:
        skipped_ids = [id for id in arxiv_ids if id in previously_translated]
        arxiv_ids = [id for id in arxiv_ids if id not in previously_translated]
        if skipped_ids:
            chatbot.append([f"跳过已翻译论文", f"以下论文已在历史记录中成功翻译：{', '.join(skipped_ids)}"])
            yield from update_ui(chatbot=chatbot, history=history)

    def generate_report(arxiv_ids, successful_ids, failed_ids, task_start_time, is_final=False):
        """生成报告，并根据是否是最终报告决定文件名"""
        task_end_time = datetime.now()
        total_duration = task_end_time - task_start_time
        avg_duration = total_duration / len(arxiv_ids) if arxiv_ids else datetime.timedelta(0)
        
        # 生成报告文件名
        if is_final:
            report_filename = f"{batch_id}-arxiv论文批量翻译报告.md"
        else:
            report_filename = f"{batch_id}-arxiv论文批量翻译报告-temp.md"
        
        report_path = os.path.join(report_folder, report_filename)
        
        # 如果存在之前的临时报告，删除它
        if not is_final:
            temp_pattern = os.path.join(report_folder, f"{batch_id}-arxiv论文批量翻译报告-temp.md")
            for old_report in glob.glob(temp_pattern):
                try:
                    os.remove(old_report)
                except Exception as e:
                    logger.error(f"Failed to remove old report {old_report}: {e}")

        # 生成报告内容
        report_content = f"""# Arxiv论文批量翻译报告

## 基本信息
| 项目 | 内容 |
|------|------|
| 报告生成时间 | {task_end_time.strftime('%y-%m-%d')} |
| 具体时间 | {task_end_time.strftime('%H:%M:%S')} |
| 任务开始时间 | {task_start_time.strftime('%y-%m-%d %H:%M:%S')} |
| 任务结束时间 | {task_end_time.strftime('%y-%m-%d %H:%M:%S')} |
| 平均每个任务用时 | {str(avg_duration).split('.')[0]} |
| 任务总计用时 | {str(total_duration).split('.')[0]} |

## 任务统计
| 统计项目 | 数量 |
|----------|------|
| 总计任务数量 | {len(arxiv_ids)} |
| 翻译成功数量 | {len(successful_ids)} |
| 翻译失败数量 | {len(failed_ids)} |

## 详细信息
### 成功翻译的论文
{chr(10).join(['- ' + id for id in successful_ids])}

### 翻译失败的论文
{chr(10).join(['- ' + id for id in failed_ids])}

### 待处理的论文
{chr(10).join(['- ' + id for id in arxiv_ids if id not in successful_ids and id not in failed_ids])}
"""
        
        # 写入报告文件
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report_content)
            logger.info(f"Report generated successfully at {report_path}")
            
            # 在生成最终报告后删除临时报告
            if is_final:
                temp_pattern = os.path.join(report_folder, f"{batch_id}-arxiv论文批量翻译报告-temp.md")
                for temp_file in glob.glob(temp_pattern):
                    try:
                        os.remove(temp_file)
                        logger.info(f"Removed temporary report file: {temp_file}")
                    except Exception as e:
                        logger.error(f"Failed to remove temporary report {temp_file}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")

    # <-------------- process each arxiv ID ------------->
    for i, arxiv_id in enumerate(arxiv_ids):
        task_start = datetime.now()
        separator = "#" * 72
        current_time = datetime.now()
        date_str = current_time.strftime('%y-%m-%d')
        time_str = current_time.strftime('%H-%M-%S')
        
        chatbot.append([f"{separator}", ""])
        chatbot.append([f"当前日期: {date_str}", f"当前时间: {time_str}"])
        chatbot.append([f"正在处理 arxiv ID ({i+1}/{len(arxiv_ids)})", f"arxiv ID: {arxiv_id}"])
        yield from update_ui(chatbot=chatbot, history=history)

        success_flag = True
        try:
            # Download and process single arxiv paper
            txt, downloaded_arxiv_id = yield from arxiv_download(chatbot, history, arxiv_id, allow_cache)
        except tarfile.ReadError as e:
            success_flag = False
            failed_ids.append(arxiv_id)
            yield from update_ui_lastest_msg(
                f"无法自动下载论文 {arxiv_id} 的Latex源码，请前往arxiv打开此论文下载页面，点other Formats，然后download source手动下载latex源码包。",
                chatbot=chatbot, history=history)
            continue

        if txt.endswith('.pdf'):
            yield from update_ui_lastest_msg(f"论文 {arxiv_id} 已经存在翻译好的PDF文档", chatbot=chatbot, history=history)
            if arxiv_id not in successful_ids:  # 防止重复添加
                successful_ids.append(arxiv_id)
            continue

        # Rest of the processing remains same as before
        # ... (keep the existing code for processing a single paper)
        if os.path.exists(txt):
            project_folder = txt
        else:
            if txt == "": txt = '空空如也的输入栏'
            yield from update_ui_lastest_msg(f"找不到本地项目或无法处理: {txt}", chatbot=chatbot, history=history)
            continue

        file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.tex', recursive=True)]
        if len(file_manifest) == 0:
            yield from update_ui_lastest_msg(f"找不到任何.tex文件: {txt}", chatbot=chatbot, history=history)
            continue

        # <-------------- if is a zip/tar file ------------->
        project_folder = desend_to_extracted_folder_if_exist(project_folder)

        # <-------------- move latex project away from temp folder ------------->
        from shared_utils.fastapi_server import validate_path_safety
        validate_path_safety(project_folder, chatbot.get_user())
        project_folder = move_project(project_folder, downloaded_arxiv_id)

        # <-------------- cloud check ------------->
        if allow_gptac_cloud_io and downloaded_arxiv_id:
            from crazy_functions.latex_fns.latex_actions import check_gptac_cloud
            success, downloaded = check_gptac_cloud(downloaded_arxiv_id, chatbot)
            if success:
                chatbot.append([
                    f"检测到GPTAC云端存在翻译版本, 如果不满意翻译结果, 请禁用云端分享, 然后重新执行。", 
                    None
                ])
                yield from update_ui(chatbot=chatbot, history=history)
                continue

        # <-------------- translate ------------->
        success_flag = True  # 初始化整体成功状态
        if not os.path.exists(project_folder + '/merge_translate_zh.tex'):
            try:
                yield from Latex精细分解与转化(file_manifest, project_folder, llm_kwargs, plugin_kwargs,
                                           chatbot, history, system_prompt, mode='translate_zh',
                                           switch_prompt=_switch_prompt_)
            except Exception as e:
                success_flag = False
                failed_ids.append(arxiv_id)  # 立即添加到失败列表
                yield from update_ui_lastest_msg(
                    f"论文 {arxiv_id} 在精细切分阶段失败: {str(e)}",
                    chatbot=chatbot, history=history)
                continue

        # <-------------- compile PDF ------------->
        try:
            yield from update_ui_lastest_msg("正在将翻译好的项目tex项目编译为PDF...", chatbot=chatbot, history=history)
            pdf_success = yield from 编译Latex(chatbot, history, main_file_original='merge',
                                        main_file_modified='merge_translate_zh', mode='translate_zh',
                                        work_folder_original=project_folder, work_folder_modified=project_folder,
                                        work_folder=project_folder)
            if not pdf_success:
                success_flag = False
        except Exception as e:
            success_flag = False
            logger.error(f"PDF compilation failed: {str(e)}")
            yield from update_ui_lastest_msg(f"PDF编译过程出错: {str(e)}", chatbot=chatbot, history=history)

        # <-------------- zip PDF ------------->
        try:
            zip_res = zip_result(project_folder)
            if success_flag:
                chatbot.append((f"论文 {arxiv_id} 处理成功", '请查收结果（压缩包）...'))
                yield from update_ui(chatbot=chatbot, history=history)
                time.sleep(1)
                promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
                successful_ids.append(arxiv_id)  # 所有步骤都成功后才添加到成功列表
            else:
                chatbot.append((f"论文 {arxiv_id} 处理失败",
                                '虽然PDF生成失败了, 但请查收结果（压缩包）, 内含已经翻译的Tex文档...'))
                yield from update_ui(chatbot=chatbot, history=history)
                time.sleep(1)
                promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
                if arxiv_id not in failed_ids:  # 避免重复添加
                    failed_ids.append(arxiv_id)
        except Exception as e:
            success_flag = False
            if arxiv_id not in failed_ids:  # 避免重复添加
                failed_ids.append(arxiv_id)
            logger.error(f"Result packaging failed: {str(e)}")
            yield from update_ui_lastest_msg(f"结果打包过程出错: {str(e)}", chatbot=chatbot, history=history)

        if success_flag:
            successful_ids.append(arxiv_id)
        else:
            failed_ids.append(arxiv_id)
            success_flag = False

        task_end = datetime.now()
        task_duration = task_end - task_start
        task_times.append(task_duration)

        # 每完成一个arxiv的翻译就生成一次临时报告
        generate_report(arxiv_ids, successful_ids, failed_ids, task_start_time, is_final=False)

    # 所有任务完成后生成最终报告
    generate_report(arxiv_ids, successful_ids, failed_ids, task_start_time, is_final=True)

    # <-------------- final message ------------->
    chatbot.append((f"批量处理完成", f'共处理了 {len(arxiv_ids)} 篇论文'))
    yield from update_ui(chatbot=chatbot, history=history)
    return True


#  =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=- 插件主程序3  =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=

@CatchException
def PDF翻译中文并重新编译PDF(txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, web_port):
    # <-------------- information about this plugin ------------->
    chatbot.append([
        "函数插件功能？",
        "将PDF转换为Latex项目，翻译为中文后重新编译为PDF。函数插件贡献者: Marroh。注意事项: 此插件Windows支持最佳，Linux下必须使用Docker安装，详见项目主README.md。目前对机器学习类文献转化效果最好，其他类型文献转化效果未知。"])
    yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面

    # <-------------- more requirements ------------->
    if ("advanced_arg" in plugin_kwargs) and (plugin_kwargs["advanced_arg"] == ""): plugin_kwargs.pop("advanced_arg")
    more_req = plugin_kwargs.get("advanced_arg", "")
    no_cache = more_req.startswith("--no-cache")
    if no_cache: more_req.lstrip("--no-cache")
    allow_cache = not no_cache
    _switch_prompt_ = partial(switch_prompt, more_requirement=more_req)

    # <-------------- check deps ------------->
    try:
        import glob, os, time, subprocess
        subprocess.Popen(['pdflatex', '-version'])
        from .latex_fns.latex_actions import Latex精细分解与转化, 编译Latex
    except Exception as e:
        chatbot.append([f"解析项目: {txt}",
                        f"尝试执行Latex指令失败。Latex没有安装, 或者不在环境变量PATH中。安装方法https://tug.org/texlive/。报错信息\n\n```\n\n{trimmed_format_exc()}\n\n```\n\n"])
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- clear history and read input ------------->
    if os.path.exists(txt):
        project_folder = txt
    else:
        if txt == "": txt = '空空如也的输入栏'
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到本地项目或无法处理: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.pdf', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.pdf文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return
    if len(file_manifest) != 1:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"不支持同时处理多个pdf文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    if plugin_kwargs.get("method", "") == 'MATHPIX':
        app_id, app_key = get_conf('MATHPIX_APPID', 'MATHPIX_APPKEY')
        if len(app_id) == 0 or len(app_key) == 0:
            report_exception(chatbot, history, a="缺失 MATHPIX_APPID 和 MATHPIX_APPKEY。", b=f"请配置 MATHPIX_APPID 和 MATHPIX_APPKEY")
            yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
            return
    if plugin_kwargs.get("method", "") == 'DOC2X':
        app_id, app_key = "", ""
        DOC2X_API_KEY = get_conf('DOC2X_API_KEY')
        if len(DOC2X_API_KEY) == 0:
            report_exception(chatbot, history, a="缺失 DOC2X_API_KEY。", b=f"请配置 DOC2X_API_KEY")
            yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
            return

    hash_tag = map_file_to_sha256(file_manifest[0])

    # # <-------------- check repeated pdf ------------->
    # chatbot.append([f"检查PDF是否被重复上传", "正在检查..."])
    # yield from update_ui(chatbot=chatbot, history=history)
    # repeat, project_folder = check_repeat_upload(file_manifest[0], hash_tag)

    # if repeat:
    #     yield from update_ui_lastest_msg(f"发现重复上传，请查收结果（压缩包）...", chatbot=chatbot, history=history)
    #     try:
    #         translate_pdf = [f for f in glob.glob(f'{project_folder}/**/merge_translate_zh.pdf', recursive=True)][0]
    #         promote_file_to_downloadzone(translate_pdf, rename_file=None, chatbot=chatbot)
    #         comparison_pdf = [f for f in glob.glob(f'{project_folder}/**/comparison.pdf', recursive=True)][0]
    #         promote_file_to_downloadzone(comparison_pdf, rename_file=None, chatbot=chatbot)
    #         zip_res = zip_result(project_folder)
    #         promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
    #         return
    #     except:
    #         report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"发现重复上传，但是无法找到相关文件")
    #         yield from update_ui(chatbot=chatbot, history=history)
    # else:
    #     yield from update_ui_lastest_msg(f"未发现重复上传", chatbot=chatbot, history=history)

    # <-------------- convert pdf into tex ------------->
    chatbot.append([f"解析项目: {txt}", "正在将PDF转换为tex项目，请耐心等待..."])
    yield from update_ui(chatbot=chatbot, history=history)
    project_folder = pdf2tex_project(file_manifest[0], plugin_kwargs)
    if project_folder is None:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"PDF转换为tex项目失败")
        yield from update_ui(chatbot=chatbot, history=history)
        return False

    # <-------------- translate latex file into Chinese ------------->
    yield from update_ui_lastest_msg("在tex项目将翻译为中文...", chatbot=chatbot, history=history)
    file_manifest = [f for f in glob.glob(f'{project_folder}/**/*.tex', recursive=True)]
    if len(file_manifest) == 0:
        report_exception(chatbot, history, a=f"解析项目: {txt}", b=f"找不到任何.tex文件: {txt}")
        yield from update_ui(chatbot=chatbot, history=history)  # 刷新界面
        return

    # <-------------- if is a zip/tar file ------------->
    project_folder = desend_to_extracted_folder_if_exist(project_folder)

    # <-------------- move latex project away from temp folder ------------->
    from shared_utils.fastapi_server import validate_path_safety
    validate_path_safety(project_folder, chatbot.get_user())
    project_folder = move_project(project_folder)

    # <-------------- set a hash tag for repeat-checking ------------->
    with open(pj(project_folder, hash_tag + '.tag'), 'w') as f:
        f.write(hash_tag)
        f.close()


    # <-------------- if merge_translate_zh is already generated, skip gpt req ------------->
    if not os.path.exists(project_folder + '/merge_translate_zh.tex'):
        yield from Latex精细分解与转化(file_manifest, project_folder, llm_kwargs, plugin_kwargs,
                                    chatbot, history, system_prompt, mode='translate_zh',
                                    switch_prompt=_switch_prompt_)

    # <-------------- compile PDF ------------->
    try:
        yield from update_ui_lastest_msg("正在将翻译好的项目tex项目编译为PDF...", chatbot=chatbot, history=history)
        success = yield from 编译Latex(chatbot, history, main_file_original='merge',
                                    main_file_modified='merge_translate_zh', mode='translate_zh',
                                    work_folder_original=project_folder, work_folder_modified=project_folder,
                                    work_folder=project_folder)
    except Exception as e:
        success = False
        logger.error(f"PDF compilation failed: {str(e)}")
        yield from update_ui_lastest_msg(f"PDF编译过程出错: {str(e)}", chatbot=chatbot, history=history)

    # <-------------- zip PDF ------------->
    try:
        zip_res = zip_result(project_folder)
        if success:
            chatbot.append((f"成功啦", '请收结果（压缩包）...'))
            yield from update_ui(chatbot=chatbot, history=history)
            time.sleep(1)
            promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
        else:
            chatbot.append((f"失败了",
                            '虽然PDF生成失败了, 但请查收结果（压缩包）, 内含已经翻译的Tex文档, 您可以到Github Issue区, 用该压缩包进行反馈如系统是Linux，请检查系统字体（见Github wiki） ...'))
            yield from update_ui(chatbot=chatbot, history=history)
            time.sleep(1)
            promote_file_to_downloadzone(file=zip_res, chatbot=chatbot)
    except Exception as e:
        logger.error(f"Result packaging failed: {str(e)}")
        yield from update_ui_lastest_msg(f"结果打包过程出错: {str(e)}", chatbot=chatbot, history=history)

    # <-------------- we are done ------------->
    return success

def zip_result(project_folder):
    """
    使用toolbox.zip_result前，修正所有文件的时间戳
    """
    import os
    from datetime import datetime
    min_timestamp = datetime(1980, 1, 1).timestamp()
    
    # 修正所有文件的时间戳
    for root, dirs, files in os.walk(project_folder):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                stat = os.stat(file_path)
                if stat.st_mtime < min_timestamp:
                    # 将时间戳设置为1980年1月1日
                    os.utime(file_path, (min_timestamp, min_timestamp))
            except Exception as e:
                logger.error(f"Failed to fix timestamp for {file_path}: {str(e)}")
                
    # 调用原有的zip_result函数
    from toolbox import zip_result as toolbox_zip_result
    return toolbox_zip_result(project_folder)