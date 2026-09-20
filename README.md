> **本仓库是 [Frrrrrrrrank/auto_job__find__chatgpt__rpa](https://github.com/Frrrrrrrrank/auto_job__find__chatgpt__rpa) 的改造分支（fork）。**
> 版权与原始贡献归原作者所有，本分支只做了「轻量化 + 油猴助手自动筛选与投递」两件事。
> 原作者原版说明见文末折叠区。

## 为什么用这个分支

原版走的是「Selenium 驱动 Chrome → 扫码登录 → 逐个投递」的 RPA 路线。本分支把主线换成了
**跑在你自己浏览器里的油猴脚本**，并补上了自动筛岗。具体好在哪：

- **没有 webdriver 指纹** —— 脚本在你已登录的真实 Edge 里运行，不启 ChromeDriver、
  不复制用户资料目录、不反复开关窗口。BOSS 直聘和 LinkedIn 的风控看不到「自动化」痕迹。
  这是本分支最核心的改动，也是它比驱动浏览器稳的根本原因。
- **装起来轻** —— 砍掉 langchain + FAISS + torch（约 2GB，还要求 Python ≤ 3.11），
  改成把简历全文直接交给大模型，不做向量检索。依赖只剩 `openai` / `selenium` / `pypdf` /
  `python-dotenv`，**兼容 Python 3.13**。
- **先筛再投，不是无脑群发** —— `matcher.py` 先给每个岗位打分：方向与技能关键词命中得分
  （出现在岗位标题里权重翻倍），资历过高、学历不符、要签证赞助、地点不对、猎头含糊的依次扣分，
  归一成 0~100 分，够门槛才投。不值得投的直接跳过，不用你一条条点开看。
- **话术每条现写** —— 结合 JD 现写招呼语，再由 `letter_variety.py` 做角度轮换 + 相似度终检，
  绝不把同一条模板复制出去，避免「一看就是群发」。
- **带安全闸门** —— 全自动档位强制带 7 道闸门（见下），撞到安全验证立刻停手交还人工，
  不做无人值守的批量轰炸。风控强的站点，这一条比投得快更重要。
- **判定可解释** —— 每个岗位「多少分、判什么、为什么」都写进日志，误杀能追溯到具体规则，
  `pipeline/skipped.csv` 专门留档复核。
- **简历和密钥不出本机** —— 配话术的本地服务只监听 `127.0.0.1`；密钥、简历、投递记录
  全部已 gitignore，不进仓库。

改造过程中参考了 [applypilot](https://github.com/yvonnehe772/applypilot) 的 human-in-the-loop 思路，
以及 [get_jobs](https://github.com/loks666/get_jobs)、
[boss_batch_push](https://github.com/loks666/boss_batch_push) 的反检测取向。

## 推荐用法：油猴助手

1. 给 Edge 装 Tampermonkey，导入 `auto_job_find/boss_ai_helper.user.js`
2. 双击 `auto_job_find/start_ai_server.bat`（保持窗口开着）
3. 进到岗位搜索结果页（BOSS 的 `/web/geek/jobs?...` 或 LinkedIn 的 `/jobs/search/...`）
4. 先点面板上的 **◐ 先演练一遍** → 日志区会逐个岗位告诉你「多少分、判定是什么、为什么」
5. 确认没问题后点 **▶ 一键自动：筛完就投**

见 `auto_job_find/油猴助手使用说明.md`（含筛选规则、安全闸门、自测方法）。

## 筛选引擎怎么判（`matcher.py`）

打分分三层：**方向关键词命中**（词表在 `matcher.py` 顶部的 `CORE_TERMS`，命中在岗位标题里
权重翻倍）→ **负面信号扣分**（资历过高、学历要求、签证赞助、地点、猎头含糊）→
**归一成 0~100 分**。≥ 门槛（默认 60）判投，只有边界分才会调一次大模型复核。

想换成自己的求职方向，改 `CORE_TERMS` / `TITLE_BOOST` / `EXCLUDE_*` 这几个常量即可。

**硬排除词只匹配岗位标题与公司名，不匹配 JD 正文和卡片全文** —— BOSS 卡片上的
福利标签含「股票期权」「培训」，拿全文匹配会把正经研发岗误杀（已有回归用例锁住）。

## 安全闸门（全自动档位必带，一个都不能拆）

本地预筛 → 引擎判定 → 每日上限（默认 40）→ 随机间隔（25~60 秒）→
撞安全验证即停 → 跨天去重不重复投 → LinkedIn 签证类问题永不代答（退回人工）。

> 权衡说明：BOSS 直聘风控很强，全自动群发模板话术极易触发安全验证。
> 因此本仓库的全自动**强制带上述闸门**，且每条话术都由模型结合 JD 现写
> （角度轮换 + 相似度终检，绝不复制同一条）。最保守的用法是只用
> 「◐ 先演练一遍」筛出清单，再手动单条投。

## 旧的自动化模式

Selenium 驱动浏览器、扫码登录、逐个投递的那套流程，本分支只作为**备选**保留，不再推荐。
配置项与运行步骤见 **[`auto_job_find/旧模式说明.md`](auto_job_find/旧模式说明.md)**，风险自担。

## 原作者原版说明

<details>
<summary>点击展开：付费版、操作步骤、assistant / langchain 模式、常见问题、其他朋友的项目</summary>

### 更加便于操作的付费版本

目前该版本已经下架，您可以在微软商店中下载sider windows客户端，找到求职大师功能，每天可以免费使用三十次（目前已经推出）；同时也将加入linkedin和Boss职位筛选功能（最晚在十月底前推出）；

### 正文

这是一个完全免费的脚本，只需要你们自己配置好openai的api即可

希望您能给我点个 **star**

如果在这个寒冷的招聘季，这个脚本能给您一些帮助，带来一些温暖，将让我非常荣幸

希望不要有人拿着我的脚本去割韭菜，都已经被逼到用这种脚本投简历的地步了，身上也没啥油水可榨了吧。

### 操作步骤

1. 请首先配置好 openai 的 api（使用.env文件或者在代码中配置）
2. 将pdf简历上传到文件夹 auto_job_find 里，命名为 **“my_cover.pdf"**
3. 将需要的包安装好
4. 执行 write_response.py

### 关于 asistant

会自动生成 openai 的 asistant，并在本地产生一个 .json 文件，只有第一次运行的时候才会产生，后面每次运行如果检测到这个 json ，就会调用已有的 asistant。

### 使用到的包

- `python-dotenv`
- `openai`
- `selenium`
- `robotframework`
- `robotframework-seleniumlibrary`
- `robotframework-pythonlibcore`
- `faiss-cpu不支持3.12（faiss-gpu不清楚）。建议大家用3.11及以下版本的python运行脚本。` from @[huanmit](https://github.com/huanmit)

### About RPA

tutorial video about how to learn [rpa](https://www.youtube.com/watch?v=65OPFmEgCbM&list=PLx4LEkEdFArgrdD_lvXe_hYBy8zM0Sp3b&index=1)

Plugin: Intellibot@Selenium Library

------------------下面是简单的教学视频---------------------

[B站链接](https://www.bilibili.com/video/BV1UC4y1N78v/?share_source=copy_web&vd_source=b2608434484091fcc64d4eb85233122d)

[油管链接](https://youtu.be/TlnytEi2lD8?si=jfcDj2MZqBptziZc)

### 运行方式

先将该项目clone到本地，然后在项目根目录下执行

```bash
pip install -r requirements.txt
```

#### assistant方式运行

打开.env文件，在里面配置好OpenAI的API key
随后将pdf简历上传到文件夹auto_job_find里，命名为“my_cover".随后执行write_response.py即可
这种方式不支持使用自定义api，优势是执行速度更快
如果需要使用自定义api，请使用下面的方式运行

#### langchain方式

同样打开.env文件，在里面配置好OpenAI的API key和你想要请求的api地址
随后将pdf简历放到文件夹resume里
最后执行write_response.py即可

#### chatgpt4 及以上运行方式

如果尝试使用更新的chatGPT则不能保持最新版本为`v1.1.1`，同时如果报错信息为`An error occurred: Error code: 400 - {'error': {'message': "The requested model 'gpt-4o-mini' cannot be used with the Assistants API in v1. Follow the migration guide to upgrade to v2: https://platform.openai.com/docs/assistants/migration.", 'type': 'invalid_request_error', 'param': 'model', 'code': 'unsupported_model'}}`

1. 需要手动将chatgpt更新到最新版

```shell
pip install --upgrade openai
```

2. 以及更改`create_assistant`中的结构体，详细参考[迁移模型](https://platform.openai.com/docs/assistants/migration)中的描述。建议直接在[平台](https://platform.openai.com/assistants/)上手动添加最新的assist然后复制代码到`assistant.json`中最为方便

```json
{"assistant_id": "asst_token"}
```

### 其他朋友基于 js / azure 构建的版本

下面这位朋友基于js实现了一个更加简易的版本——虽然因为调用的免费api，无法使用assistant进行retrival，需要自己对简历进行简单的处理，但原作者依然认为这是个很棒的项目。

感谢朋友的贡献：

- [noBaldAaa/find-job](https://github.com/noBaldAaa/find-job)
- [LouisCaixuran/auto_job_find_azure](https://github.com/LouisCaixuran/auto_job_find_azure)

</details>
