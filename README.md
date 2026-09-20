> **本仓库是 [Frrrrrrrrank/auto_job__find__chatgpt__rpa](https://github.com/Frrrrrrrrank/auto_job__find__chatgpt__rpa) 的改造分支（fork）。**
> 在原项目基础上做了「轻量化 + 油猴助手自动筛选与投递」的改造，版权与原始贡献归原作者所有。
> 改造内容见下方《本仓库改造说明》，其下为原作者原版说明。

作者已经去打工了，现在在sider做产品经理，义务为我们的产品打一下广告，各位见谅
[![banner](https://github.com/Frrrrrrrrank/auto_job__find__chatgpt__rpa/assets/82270228/0fff88f5-670c-4126-b6aa-3df1763fe757)](https://sider.ai/ad-land-redirect?source=github&p1=mi&p2=kk)

欢迎大家使用

## 本仓库改造说明（重要）

在本机对原项目做了大幅改造，核心变化：

| 变化 | 说明 |
|---|---|
| 轻量化 | 去掉 langchain + FAISS + torch（约 2GB），改为直接把简历全文交给大模型（openai + pypdf） |
| 兼容 Python 3.13 | 原 faiss-cpu 等依赖不支持新版本 Python |
| **油猴助手（推荐路线）** | 新增 `boss_ai_helper.user.js`：在你**真实浏览器**里跑，不驱动浏览器 → 没有 webdriver 指纹，不反复开关窗口、不触发驱动反检测。支持 BOSS 直聘 + LinkedIn |
| **自动筛选 + 自动投递（v2.0）** | 新增 `matcher.py` 岗位筛选引擎（可解释打分 + 边界分模型复核）。在岗位列表页点一下，它自己逐张卡片读 JD → 判断值不值得投 → 值得就写一条定制话术并投出去 |
| 本地服务 | `serve_letter.py` 提供 `/health` `/letter` `/match` `/record` 四个接口，只监听 127.0.0.1 |
| 半自动投递 | 参考了 [applypilot](https://github.com/yvonnehe772/applypilot) 的 human-in-the-loop 思路，保留 `semi_auto.py` 作为人工逐条确认的备选 |
| 参考项目 | [get_jobs](https://github.com/loks666/get_jobs)、[boss_batch_push](https://github.com/loks666/boss_batch_push) 的思路借鉴（油猴脚本 + 反检测取向） |

### 推荐用法：油猴助手

1. 给 Edge 装 Tampermonkey，导入 `auto_job_find/boss_ai_helper.user.js`
2. 双击 `auto_job_find/start_ai_server.bat`（保持窗口开着）
3. 进到岗位搜索结果页（BOSS 的 `/web/geek/jobs?...` 或 LinkedIn 的 `/jobs/search/...`）
4. 先点面板上的 **◐ 先演练一遍** → 日志区会逐个岗位告诉你「多少分、判定是什么、为什么」
5. 确认没问题后点 **▶ 一键自动：筛完就投**

见 `auto_job_find/油猴助手使用说明.md`（含筛选规则、安全闸门、自测方法）。

### 筛选引擎怎么判（`matcher.py`）

核心方向词命中计分（生信 / 计算生物 / 多组学 / 单细胞 / 空间转录组 / 免疫组库 /
肿瘤免疫 / 新抗原 / NGS …，命中在标题里权重翻倍）→ 再按资历过高、学历要求、
签证赞助、地点、猎头含糊等扣分 → 归一成 0~100 分。
≥ 门槛（默认 60）判投，边界分才会调一次大模型复核。

**硬排除词只匹配岗位标题与公司名，不匹配 JD 正文和卡片全文** —— BOSS 卡片上的
福利标签含「股票期权」「培训」，拿全文匹配会把正经研发岗误杀（已有回归用例锁住）。

### 安全闸门（全自动档位必带，一个都不能拆）

本地预筛 → 引擎判定 → 每日上限（默认 40）→ 随机间隔（25~60 秒）→
撞安全验证即停 → 跨天去重不重复投 → LinkedIn 签证类问题永不代答（退回人工）。

> 权衡说明：BOSS 直聘风控很强，全自动群发模板话术极易触发安全验证。
> 因此本仓库的全自动**强制带上述闸门**，且每条话术都由模型结合 JD 现写
>（角度轮换 + 相似度终检，绝不复制同一条）。最保守的用法是只用
>「◐ 先演练一遍」筛出清单，再手动单条投。

### 旧的自动化模式（write_response.py，风险自担）

## 本机已配置好的运行方式（轻量版）

原版依赖 langchain + FAISS + torch（约 2GB，且需要 Python ≤3.11），已改为**轻量方案**：
直接把整份简历文本交给大模型，不需要向量检索。依赖只有 openai / selenium / pypdf / python-dotenv。

1. 打开 `auto_job_find/.env`，填入你的密钥（原 key 已余额不足）和姓名、联系方式
2. 把**你自己的** PDF 简历放进 `auto_job_find/resume/`（目录里现在那份是仓库自带的样本，不是你的）
3. 先保持 `DRY_RUN=1` 试跑一次，看生成的打招呼语满不满意
   ```bash
   cd auto_job_find
   ..\venv 解释器 write_response.py      # 或双击 run.bat
   ```
4. 满意后把 `DRY_RUN` 改成 `0`，程序会打开 Chrome → 扫码登录 Boss 直聘 → 自动逐个投递
5. 每次运行的投递记录在 `auto_job_find/logs/` 下

改提示词口吻：编辑 `auto_job_find/prompts.py` 里的 `LETTER_SYSTEM_PROMPT` / `LETTER_USER_TEMPLATE`。

## 更加便于操作的付费版本

目前该版本已经下架，您可以在微软商店中下载sider windows客户端，找到求职大师功能，每天可以免费使用三十次（目前已经推出）；同时也将加入linkedin和Boss职位筛选功能（最晚在十月底前推出）；


## 正文
这是一个完全免费的脚本，只需要你们自己配置好openai的api即可

希望您能给我点个 **star**

如果在这个寒冷的招聘季，这个脚本能给您一些帮助，带来一些温暖，将让我非常荣幸

希望不要有人拿着我的脚本去割韭菜，都已经被逼到用这种脚本投简历的地步了，身上也没啥油水可榨了吧。

## 操作步骤

1. 请首先配置好 openai 的 api（使用.env文件或者在代码中配置）
2. 将pdf简历上传到文件夹 auto_job_find 里，命名为 **“my_cover.pdf"**
3. 将需要的包安装好
4. 执行 write_response.py

## 关于 asistant

会自动生成 openai 的 asistant，并在本地产生一个 .json 文件，只有第一次运行的时候才会产生，后面每次运行如果检测到这个 json ，就会调用已有的 asistant。

## 使用到的包

- `python-dotenv`
- `openai`
- `selenium`
- `robotframework`
- `robotframework-seleniumlibrary`
- `robotframework-pythonlibcore`
- `faiss-cpu不支持3.12（faiss-gpu不清楚）。建议大家用3.11及以下版本的python运行脚本。` from @[huanmit](https://github.com/huanmit)

## About RPA

tutorial video about how to learn [rpa](https://www.youtube.com/watch?v=65OPFmEgCbM&list=PLx4LEkEdFArgrdD_lvXe_hYBy8zM0Sp3b&index=1)

Plugin: Intellibot@Selenium Library

------------------下面是简单的教学视频---------------------

[B站链接](https://www.bilibili.com/video/BV1UC4y1N78v/?share_source=copy_web&vd_source=b2608434484091fcc64d4eb85233122d)

[油管链接](https://youtu.be/TlnytEi2lD8?si=jfcDj2MZqBptziZc)

## 运行方式
先将该项目clone到本地，然后在项目根目录下执行
```bash
pip install -r requirements.txt
```

### assistant方式运行
打开.env文件，在里面配置好OpenAI的API key
随后将pdf简历上传到文件夹auto_job_find里，命名为“my_cover".随后执行write_response.py即可
这种方式不支持使用自定义api，优势是执行速度更快
如果需要使用自定义api，请使用下面的方式运行

### langchain方式
同样打开.env文件，在里面配置好OpenAI的API key和你想要请求的api地址
随后将pdf简历放到文件夹resume里
最后执行write_response.py即可


### chatgpt4 及以上运行方式
如果尝试使用更新的chatGPT则不能保持最新版本为`v1.1.1`，同时如果报错信息为`An error occurred: Error code: 400 - {'error': {'message': "The requested model 'gpt-4o-mini' cannot be used with the Assistants API in v1. Follow the migration guide to upgrade to v2: https://platform.openai.com/docs/assistants/migration.", 'type': 'invalid_request_error', 'param': 'model', 'code': 'unsupported_model'}}`

1. 需要手动将chatgpt更新到最新版，

```shell
pip install --upgrade openai
```

2. 以及更改`create_assistant`中的结构体，详细参考[迁移模型](https://platform.openai.com/docs/assistants/migration)中的描述。建议直接在[平台](https://platform.openai.com/assistants/)上手动添加最新的assist然后复制代码到`assistant.json`中最为方便.
```json
{"assistant_id": "asst_token"}
```


------------下面是其他朋友基于js构建的更加易于使用的代码---------------

我一直也在考虑如何可以降低各位的使用门槛，基于现在项目的热度，我发现很多朋友都需要这个东西来帮助自己，但是我相信对于更多的人而言，甚至vpn都是一个障碍

下面这位朋友基于js实现了一个更加简易的版本，虽然因为调用的免费api，无法使用assistant进行retrival，需要自己对简历进行简单的处理，但我依然认为这是个很棒的项目

感谢朋友的贡献，以下是链接：

[https://github.com/noBaldAaa](https://github.com/noBaldAaa/find-job)https://github.com/noBaldAaa/find-job

------------下面是其他朋友基于azure的openai api构建的版本的更加易于使用的代码---------------
https://github.com/LouisCaixuran/auto_job_find_azure


