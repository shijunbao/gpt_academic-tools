# 基于arxivid的批量翻译论文的修改文件
## 版本
当前适配的版本为：学术gpt3.9

2024-1129版本：
打磨多日，基本上可以连续的连续的批量翻译了
增加了report目录的读取功能，翻译过的arxiv论文自动跳过。


01 版本：   
初始的批处理，输入多个 arxivid，用英文逗号隔开，实现了批量下载，批量翻译。
下载的时候如果报错，则自动进行下一个 arxivid 的处理。

## 用法
解压缩文件，拷贝Latex_Function.py拷贝到学术gpt根目录下的crazy_functions目录中，替换原文件即可。

输入arxivid的时候，可以实现多个id批量翻译，输入框输入：2411.02018,2411.01929,2411.01870,2411.01851  这样的多个id，用英文逗号隔开即可。
点击插件区的翻译按钮即可。


# 翻译好的pdf文件批量改名
## 这个小工具的功能：
可以读取翻译好的输出的pdf论文的前两页，然后根据论文内容将学术gpt的翻译好的pdf文件改名，名字包含论文的核心内容和作者使用的技术的英文名称
格式为：2024-04-19-16-07-50-xxxxxxx.pdf

## 批量改名工具的用法
解压，将批量起名程序放到含有pdf文件的目录，
gptacademic翻译后的第一次起名：双击pdf翻译后的文件改名.py进行基于大模型的批量起名。
这个文件只对翻译完毕，带有-merge_translate_zh.pdf 字样的pdf进行起名，其他名称pdf无效。
。
起名后不满意，可以修改 pdf翻译后的文件改名-再一次全部重命名.py 中的提示词，双击 pdf翻译后的文件改名-再一次全部重命名.py 进行再次的起名。
这个文件对于全部的起名后的pdf文件进行重命名。


注意：这两个文件  都会删除cmparison.pdf中英文对照版本，只对translation_zh的纯中文的pdf进行改名。

## 使用前需要配置：
apikey    
模型服务器地址
模型名称
目前测试   本地vllm 和 在线的deepseek 可用。  其他需要自己尝试。
