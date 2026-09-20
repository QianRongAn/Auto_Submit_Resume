# Auto_Submit_Resume

在真实浏览器里自动筛选岗位并投递的油猴脚本，支持 BOSS 直聘和 LinkedIn。判断规则可解释，全自动档位强制带安全闸门。

> 本仓库是 [Frrrrrrrrank/auto_job__find__chatgpt__rpa](https://github.com/Frrrrrrrrank/auto_job__find__chatgpt__rpa) 的改造分支（fork），版权与原始贡献归原作者所有。
> 本分支只做了两件事：把依赖做轻，以及加上在真实浏览器里运行的自动筛选与投递。
> 原作者原版说明见文末折叠区。

## 它解决什么问题

海投有个绕不开的矛盾。投太少没机会，投太多容易被风控盯上，而且列表里绝大多数岗位跟你不匹配，一条条点开看纯粹浪费时间。

这个项目把流程拆成三步：先判断岗位值不值得投，再为值得投的岗位现写一条招呼语，最后按节奏投出去。整个过程跑在你自己已经登录的浏览器里，不用交出账号密码，也不用装浏览器驱动。

## 为什么是油猴脚本

原版走的是 Selenium 驱动 Chrome、扫码登录、逐个投递的路线。这套流程能跑，但有两个问题：浏览器被程序控制，BOSS 和 LinkedIn 的风控识别得出来；而且原版为了做简历向量检索，引入了 langchain、FAISS、torch 三个大依赖，装完接近 2GB，还卡在 Python 3.11 以下。

本分支把主线换成了油猴脚本，具体差别如下。

**没有 webdriver 指纹。** 脚本是注入到页面里的一段 JS，不发 CDP 指令，不启动 ChromeDriver，不复制用户资料目录，也不反复开关窗口。风控看到的行为就是一个普通用户在正常翻页。这是它比驱动浏览器稳的根本原因，也是本分支最核心的改动。

**依赖从 2GB 降到 4 个包。** 简历不再做向量检索，整份文本直接交给大模型。现在只需要 `openai`、`selenium`、`pypdf`、`python-dotenv`，Python 3.13 也能跑。

**先筛再投。** 引擎会先给每个岗位打分，不值得投的直接跳过，你不需要一条条点开看。打分依据不只是关键词，具体见下一节。

**招呼语按岗位现写。** 每条话术都由模型结合该岗位的 JD 现场生成，再由 `letter_variety.py` 做角度轮换和相似度终检，避免几十条内容互相撞车。

**带安全闸门。** 全自动档位强制走 7 道闸门，撞到安全验证就停手交还人工，不做无人值守的批量投递。

**判定可解释。** 每个岗位得了多少分、判成什么、为什么，全部写进日志，误杀可以追溯到具体规则。`pipeline/skipped.csv` 专门留档，方便回头复核。

**简历和密钥不出本机。** 负责写话术的本地服务只监听 `127.0.0.1`，密钥、简历、投递记录都已 gitignore，不会进仓库。筛选规则同样如此，你的求职方向、学历、经历写在 `matcher_profile.json` 里，这份文件也在 gitignore 中。

改造过程中参考了 [applypilot](https://github.com/yvonnehe772/applypilot) 的 human-in-the-loop 思路，以及 [get_jobs](https://github.com/loks666/get_jobs)、[boss_batch_push](https://github.com/loks666/boss_batch_push) 在反检测上的做法。

## 筛选引擎怎么判断一个岗位

引擎在 `auto_job_find/matcher.py`，把每个岗位归一成 0 到 100 分，每一步都能说出理由。它不是拿简历里的关键词去简单比对，而是分三步走。

### 第一步：硬排除

只查岗位标题和公司名。命中任何一个词就直接判 0 分，不再进入后面的计算。

标题里带这些词的会被拦掉：销售、保险、房产、中介、招生、导购、贷款、理财、催收、客服、服务员、司机、普工、主播、保安、保洁、厨师、快递、仓管、收银、前台、文员、行政、人事、会计、出纳、法务、审计、教师、护士、医师、药师等，中英文各有一张表。公司名里带「人力资源」「劳务」「人才服务」「招聘」的也一并排除。

还有几个词只做软扣分，比如实习、兼职、应届、管培，因为它们偶尔也会出现在正经岗位里。

### 第二步：算分

| 维度 | 分值 |
|---|---|
| 方向与技能词命中 | 最多加 78 分，同样的词出现在标题里权重翻倍 |
| 标题强化词命中 | 命中加 18 分 |
| 资历档位 | 首席级扣 45，总监级扣 40，专家级扣 35，高级或 lead 扣 8 |
| 学历要求 | 明确要求博士扣 18，比配置里的学历高一档 |
| 年限要求 | 要求 3 年扣 12，5 年扣 28，8 年以上扣 45，均与配置里的年限对比 |
| 地点与签证 | 扣 15 到 35 |
| 猎头、外包、驻场 | 扣 12 |
| INCLUDE 门槛 | 没命中扣 35，只有配了 `INCLUDE_KEYWORDS` 才生效 |
| 目标区域 | 配了 `TARGET_REGIONS` 时，地点不在区域内扣分，不配就是不限 |

方向词表和标题强化词表都在配置文件里，想换行业改配置即可，逻辑一行都不用动。具体见下一节。

### 第三步：分档

- `apply`：分数达到阈值，默认 60 分，投。
- `review`：落在 35 到 59 的边界分，规则吃不准。
- `skip`：硬排除命中，或者分数低于下限，不投。

只有 `review` 档才会调一次大模型复核，这样不会每个岗位都花一次 token。复核时会把岗位信息和候选人事实一起交给模型，让它给出适配度和是否该投的结论。模型调用失败或者超时会回退到规则判定，不会卡住整个流程。

### 为什么排除词只匹配标题和公司名

BOSS 的岗位卡片尾部会挂一串福利标签，像「培训」「五险一金」「带薪年假」这类字样。如果把整张卡片甚至 JD 正文都拿来匹配排除词，这些福利描述就会和排除词撞上，一个正经的研发岗可能因此被判成销售类岗位跳过。标题短、信息密度高，用它判排除最稳。这个坑有回归用例锁着，改词表或者改选择器时都会跑到。

## 换成你自己的求职方向

方向词、排除词、候选人事实都不在代码里，而在 `auto_job_find/matcher_profile.json`。仓库里带了一份示例 `matcher_profile.example.json`（按数据分析方向写的），复制成 `matcher_profile.json` 再改即可。

加载顺序是三选一：`matcher_profile.json` 优先，没有就用示例配置，两个都没有就用一套最小规则并打印提示。`matcher_profile.json` 已经写进 `.gitignore`，你自己的方向词和经历不会进仓库。

| 字段 | 作用 | 怎么改 |
|---|---|---|
| `PROFILE` | 学历、年限、方向、技能 | 填自己的情况。年限和学历会拿去和 JD 里要求的对比 |
| `CORE_TERMS` | 方向词表，格式 `[正则, 权重, 展示名]` | 权重越高代表这个词越能代表你的方向，出现在标题里时加倍 |
| `TITLE_BOOST` | 出现在标题里额外加 18 分的词 | 一般是 `CORE_TERMS` 里最能代表方向的几个 |
| `EXCLUDE_HARD` | 标题命中直接判 0 分 | 只写绝对不可能投的岗位类型，别写「不优先考虑」的 |
| `EXCLUDE_COMPANY` | 公司名命中直接判 0 分 | 比如「人力资源」「劳务」「人才服务」 |
| `SENIORITY_BLOCK` | 资历偏高的扣分档 | 按自己的年限调，别让资深岗位把分数稀释掉 |
| `DEGREE_PHD` | 学历门槛偏高的扣分项 | 不介意投要求博士的岗位，删掉这一段即可 |
| `LLM_PROMPT` | 边界分交给模型复核时的提示词 | 里面写候选人事实和判断口径，保留三个占位符 |
| `SELFTEST` | 自测用例 | 跟着方向词表一起改，用来验证判定是否符合预期 |

改完跑一遍自测：

```bash
cd auto_job_find
python matcher.py --selftest
```

九个用例全过，说明词表内部自洽。输出第一行会告诉你当前用的是哪份配置。

有一点要留意：`.env` 里的 `EXCLUDE_KEYWORDS` 只能往排除表里追加词，删词或者整体换方向，得改配置文件。

## 支持的站点

| 站点 | 列表页 |
|---|---|
| BOSS 直聘 | `https://www.zhipin.com/web/geek/jobs?query=...&city=...` |
| LinkedIn 中国版 | `https://www.linkedin.com/jobs/search/?keywords=...` |

两个站点的卡片结构不一样，脚本内部分别适配。标题关键词中英文都写了，因为领英在中国区界面下正文经常是中文。

注意 BOSS 的路径是 `jobs` 复数，写成单数 `job` 会零结果或者跳回首页。城市参数直接打开 BOSS 搜索页，从地址栏里 `city=` 后面照抄即可。

## 用法

### 一、装好三样东西

1. **浏览器扩展。** 给 Edge 或 Chrome 装 Tampermonkey，再把 `auto_job_find/boss_ai_helper.user.js` 拖进浏览器窗口，Tampermonkey 会弹出安装页，点「安装」。
2. **Python 依赖。** 执行 `pip install openai selenium pypdf python-dotenv`，Python 3.9 及以上都可以。
3. **两份配置文件。** 都在 `auto_job_find/` 目录下，各复制一份、去掉 `.example` 后缀：
   - `.env.example` 复制成 `.env`，至少填 `OPENAI_API_KEY`。用中转服务的话，把 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 一并改掉。
   - `matcher_profile.example.json` 复制成 `matcher_profile.json`，按自己的方向改 `CORE_TERMS`。这一步决定「什么岗位算匹配」，不配也能跑，但用的就是示例里的数据分析方向。

### 二、起本地服务

双击 `auto_job_find/start_ai_server.bat`，命令行窗口保持开着。它负责生成招呼语和打分，只监听 `127.0.0.1:8765`，数据不出本机。想确认它活着，浏览器打开 `http://127.0.0.1:8765/health` 会返回一个 ok。

### 三、先演练，不要直接投

1. 在浏览器里打开 BOSS 的岗位搜索页，或者领英的岗位搜索页，正常登录。
2. 页面角落会出现脚本面板，带一个日志区。
3. 点 **◐ 先演练一遍**。脚本逐个读岗位卡片，把标题、公司、JD 交给打分引擎，日志区实时打出「多少分、判成什么、为什么」。
4. 对着日志调参，这一步最值得花时间：
   - 明显该投的被判 skip，多半是方向词没配全，回 `CORE_TERMS` 补词。
   - 一堆不相关的被判 apply，往上调 `MATCH_THRESHOLD`，或者往 `EXCLUDE_HARD` 里加词。
   - 改完刷新页面重跑演练即可，不用重启本地服务。

### 四、确认无误再开自动

点 **▶ 一键自动：筛完就投**。之后的顺序是：筛出 apply 档的岗位，逐个生成招呼语，按 25 到 60 秒的随机间隔发出去，达到当日上限就停。中途撞到安全验证、滑块或者扫码，脚本会停下来把控制权交回给你。

第一次跑，建议先把 `.env` 里的 `DRY_RUN` 设成 1，只走判定和话术生成，不真的发出去。

### 五、在哪看结果

- `pipeline/applied.csv`：投出去的岗位，含时间、公司、岗位、分数和实际发出去的招呼语。
- `pipeline/skipped.csv`：被跳过的岗位，含命中的排除词或扣分理由，用来复核有没有误杀。
- 面板日志区是当次运行的实时输出，关掉页面就没了，历史以那两个 CSV 为准。

更细的说明见 `auto_job_find/油猴助手使用说明.md`，里面有筛选规则的逐条讲解、安全闸门的实现方式，以及不碰账号的离线自测方法。

## 安全闸门

全自动档位强制走这 7 道，一道都不能拆：

1. 本地预筛，先把明显不合适的剔掉。
2. 引擎判定，走上面那套打分。
3. 每日上限，默认 40 条。
4. 随机间隔，默认两条之间隔 25 到 60 秒。
5. 撞到安全验证立刻停止，交还人工。
6. 跨天去重，同一个岗位不会重复投。
7. LinkedIn 的签证类问题不代答，一律退回人工。

BOSS 的风控很强，全自动群发模板话术很容易触发验证，所以这套闸门是硬性的。同时每条招呼语都由模型结合 JD 现写，写完再做相似度终检，不会出现几十条一模一样的内容。最保守的用法是只用「先演练一遍」筛出清单，再手动单条投。

## 配置

`auto_job_find/.env.example` 列了全部 33 项配置，复制成 `.env` 填自己的值即可。常用的几项：

| 键 | 作用 |
|---|---|
| `OPENAI_API_KEY` | 大模型密钥 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 接口地址和模型名，换中转或换模型改这两行 |
| `CANDIDATE_NAME` / `CANDIDATE_CONTACT` | 署名和联系方式，会写进招呼语 |
| `MATCH_THRESHOLD` | 投递门槛分，默认 60 |
| `DAILY_CAP` | 每日投递上限，默认 40 |
| `SEND_MIN_INTERVAL` / `SEND_MAX_INTERVAL` | 两条之间的随机间隔秒数 |
| `DRY_RUN` | 设成 1 只演练不真投，第一次务必先这样跑 |
| `INCLUDE_KEYWORDS` | 要求岗位必须命中的词，留空则不生效。填了就变成硬门槛，要填请中英文都写 |
| `EXCLUDE_KEYWORDS` | 临时追加的排除词，只能加不能删。整体换方向请改 `matcher_profile.json` |
| `TARGET_REGIONS` | 只投这些城市，留空表示不限 |

## 旧的自动化模式

Selenium 驱动浏览器、扫码登录、逐个投递的那套流程，本分支只作为备选保留，不再推荐。配置项和运行步骤见 **[`auto_job_find/旧模式说明.md`](auto_job_find/旧模式说明.md)**，风险自担。

## 原作者原版说明

<details>
<summary>点击展开：操作步骤、assistant / langchain 模式、常见问题、其他朋友的项目</summary>

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

下面这位朋友基于js实现了一个更加简易的版本。因为调用的免费api，无法使用assistant进行retrival，需要自己对简历进行简单的处理，但原作者依然认为这是个很棒的项目。

感谢朋友的贡献：

- [noBaldAaa/find-job](https://github.com/noBaldAaa/find-job)
- [LouisCaixuran/auto_job_find_azure](https://github.com/LouisCaixuran/auto_job_find_azure)

</details>
