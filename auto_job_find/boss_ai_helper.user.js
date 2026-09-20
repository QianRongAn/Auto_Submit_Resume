// ==UserScript==
// @name         AI 求职助手（BOSS 直聘 + LinkedIn）· 自动筛选投递
// @namespace    qianrongan.job
// @version      2.0.0
// @description  在你真实浏览器里自动扫岗位、筛岗位、写话术、投递。不另开浏览器、不驱动浏览器，因此没有 webdriver 指纹。
// @author       QianRongAn
// @match        https://www.zhipin.com/*
// @match        https://www.linkedin.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

/*
 * 设计原则（重要，别改歪了）：
 *
 *   1. 这个脚本跑在你真实的浏览器页面里 —— 真实登录态、真实指纹。
 *      它不做任何"自动化驱动"，所以招聘平台反爬针对的 webdriver 特征根本不存在。
 *
 *   2. 全自动档位（③）的流程是：逐张卡片 → 读 JD → 问本地引擎「这个岗位值不值得投」
 *      → 不值得就跳过并记下原因 → 值得就写一条定制话术并投出去。
 *      也就是说，**筛和投都是它自己做**，你只负责按一下开始。
 *
 *   3. 但"全自动"不等于"莽"：每日上限、随机间隔、已投去重、撞验证立刻停，
 *      这四个闸门一个都不能拆。宁可少投几个，也不能把账号跑废。
 *
 *   4. 页面结构一变选择器就可能失效，所以内置了「调试导出」按钮，
 *      把页面结构复制出来就能快速修好。
 */

(function () {
  'use strict';

  // ==================================================================
  // 站点配置：BOSS 和 LinkedIn 的页面结构、投递动作完全不同，分开描述
  // ==================================================================
  const SITES = {
    zhipin: {
      key: 'zhipin',
      name: 'BOSS 直聘',
      host: 'zhipin.com',
      listRe: /\/web\/geek\/jobs?/,
      defaultScene: 'zh_job',

      // 读取 JD：先按标题文字定位（改版时标题比 class 稳），再按选择器兜底
      jdHeadings: ['职位描述', '岗位职责', '任职要求', '职位要求', '工作职责',
        '任职资格', '岗位要求', '工作内容', '职位详情', '岗位描述'],
      jdSelectors: ['.job-detail-section', '.job-detail', '.job-sec-text', '.detail-content',
        '.job-detail-content', '[class*="job-detail"]', '[class*="jobdetail"]',
        '[class*="detail-content"]', '[class*="job-sec"]'],
      titleSelectors: ['.job-name', '.name', 'h1', '[class*="job-title"]', '[class*="jobName"]'],

      // 岗位卡片（列表页左侧）
      cardSelectors: ['li.job-card-wrapper', 'li.job-card-box', '.job-list-box li',
        'ul.job-list li', '[class*="job-card-wrapper"]', '[class*="job-card"]'],
      cardTitle: ['.job-name', '[class*="job-name"]', 'a[href*="job_detail"] span'],
      cardCompany: ['.company-name', '[class*="company-name"]'],
      cardMeta: ['.job-area', '[class*="job-area"]', '.salary', '[class*="salary"]'],

      // 打招呼：需要先点「立即沟通」把聊天框打开
      greetSelectors: ['.btn-startchat', 'a.btn-startchat', '[class*="startchat"]',
        '[class*="start-chat"]', '[class*="btn-greet"]', '[class*="op-btn-chat"]'],
      greetTexts: ['立即沟通', '沟通', '打招呼'],

      inputSelectors: ['textarea', '[contenteditable="true"]', '.chat-input',
        '#chat-input', '[class*="chat-input"]', '[class*="input-area"] textarea'],
      sendSelectors: [],
      sendTexts: ['发送', '发送消息'],

      verifyHints: ['安全验证', '异常访问', 'verify.html', 'security-check',
        '请完成验证', '滑动验证', '点击按钮进行验证'],
      loggedInSelectors: ['li.nav-figure', '.nav-figure', '[class*="user-nav"]'],
    },

    linkedin: {
      key: 'linkedin',
      name: 'LinkedIn',
      host: 'linkedin.com',
      listRe: /\/jobs\/(search|collections)/,
      defaultScene: 'en_cover',

      // ⚠ 领英中国版界面是中文，JD 正文也常是中文 —— 中英标题必须都有，
      //    只写英文会导致「没读到岗位 JD」。匹配时也不再要求完全相同，见 jdFromDom。
      jdHeadings: ['About the job', 'Job description', 'Responsibilities',
        'Qualifications', 'About this role', 'What you',
        '职位描述', '岗位职责', '任职要求', '工作职责', '职位要求', '岗位要求',
        '工作内容', '职位详情', '岗位描述', '任职资格', '技能要求', '学历要求',
        '关于该职位', '你将要做什么', '我们希望'],
      // ⚠ 顺序就是优先级：命中即用，所以「JD 正文元素」必须排在
      //    「会夹带职位标题/展开按钮/推荐岗位的大容器」前面。
      //    实测数据见 .workbuddy/probe/（用真实领英岗位页跑出来的）。
      jdSelectors: [
        // —— 第一档：纯正文，最干净 ——
        '.show-more-less-html__markup',   // 实测：领英中国版 JD 正文就在这
        '.jobs-description__content',
        '.jobs-box__html-content',
        '#job-details',
        '.jobs-description-content__text',
        '.description__text--rich',
        '.description__text',
        // —— 第二档：描述区容器（可能夹带「展开/收起」按钮文字，由 cleanJd 清掉）——
        '.jobs-description',
        '.jobs-details__main-content',
        '[class*="jobs-description"]',
        '[class*="description__text"]',
        '[class*="_description_"]',
        '[class*="show-more-less-html"]',
        '[data-testid*="expandable-text"]',
        '[data-testid*="description"]',
        // —— 第三档：大容器兜底，噪声最多 ——
        '[class*="job-details"]',
        'article'],
      titleSelectors: [
        '.job-details-jobs-unified-top-card__job-title',
        '.jobs-unified-top-card__job-title',
        '.job-details-jobs-unified-top-card__job-title-link',
        'h1.t-24', 'h2.t-24', '.topcard__title', 'h1',
      ],

      // 岗位卡片（搜索结果左栏）。li[data-occludable-job-id] 是现行主结构，
      // 后面几个是历史版本兜底。
      cardSelectors: ['li[data-occludable-job-id]', 'li.scaffold-layout__list-item',
        'li.jobs-search-results__list-item', '.jobs-search-results__list-item',
        'li[data-job-id]', '.job-card-container'],
      cardTitle: ['.job-card-list__title', '.job-card-list__title--link',
        'a.job-card-container__link', 'a[class*="job-card"]', 'strong'],
      cardCompany: ['.artdeco-entity-lockup__subtitle',
        '.job-card-container__primary-description',
        '.job-card-container__company-name'],
      cardMeta: ['.job-card-container__metadata-item',
        '.artdeco-entity-lockup__caption', '.job-card-container__footer-item'],

      // LinkedIn 不需要"点沟通"：Easy Apply 弹窗直接点「申请」按钮
      greetSelectors: [],
      greetTexts: [],

      // LinkedIn 的「申请」按钮（Easy Apply 和跳转官网两种，后面会区分）
      applySelectors: ['button.jobs-apply-button',
        'button[data-live-test-job-apply-button]',
        'button[aria-label*="Easy Apply"]',
        'button[aria-label*="易申请"]',
        'button[aria-label^="申请"]',
        '.jobs-s-apply button'],

      inputSelectors: [
        // Easy Apply 弹窗里的求职信 / 补充信息
        '.jobs-easy-apply-modal textarea',
        '.artdeco-modal textarea',
        '[role="dialog"] textarea',
        'textarea#additional-information',
        'textarea[id*="additional"]',
        'textarea[id*="cover"]',
        // 加好友备注（300 字符硬限）
        'textarea#custom-message',
        '#connect-cta-form__message',
        'textarea[name="message"]',
        '[role="dialog"] [contenteditable="true"]',
        // 站内信 / 聊天
        '.msg-form__contenteditable',
        '.msg-form [contenteditable="true"]',
        '[contenteditable="true"][role="textbox"]',
      ],
      sendSelectors: [
        // 加好友 / 站内信 —— 一步就能发出
        'button[aria-label="Send invitation"]',
        'button[aria-label="Send now"]',
        'button[aria-label="Send without a note"]',
        'button.msg-form__send-button',
        'button[aria-label="Send"]',
      ],
      sendTexts: ['Send', 'Submit application'],

      verifyHints: ['unusual activity', 'security verification',
        "let's do a quick security check", '/checkpoint/', 'captcha'],
      loggedInSelectors: ['img.global-nav__me-photo', '.global-nav__me', '#global-nav'],
    },
  };

  const CFG = {
    server: 'http://127.0.0.1:8765',
    scenes: {
      zh_job: { label: 'BOSS 招呼语（中文）', limit: 300, hard: null, site: 'zhipin' },
      en_cover: { label: 'LinkedIn 求职信（英文）', limit: 200, hard: null, site: 'linkedin', words: true },
      en_note: { label: 'LinkedIn 加好友备注（英文·300字符）', limit: 280, hard: 300, site: 'linkedin' },
      en_phd: { label: '博士套磁邮件（英文）', limit: 240, hard: null, site: 'linkedin', multiline: true },
    },
    defaultCap: 40,        // 每日自动发送上限
    defaultSendMin: 25,    // 连发时每条之间随机等待下限（秒）
    defaultSendMax: 60,    // 上限
    defaultThreshold: 60,  // 匹配分达到多少才投
    maxPages: 3,           // 自动模式下最多翻几页岗位列表
  };

  function detectSite() {
    const h = location.host;
    if (h.includes('zhipin.com')) return SITES.zhipin;
    if (h.includes('linkedin.com')) return SITES.linkedin;
    return null;
  }
  const SITE = detectSite();

  // ==================================================================
  // 状态
  // ==================================================================
  const sceneKey = (site) => 'scene:' + (site ? site.host : 'default');
  const APPLIED_KEY = 'appliedIds';

  const S = {
    scene: SITE ? GM_getValue(sceneKey(SITE), SITE.defaultScene) : 'zh_job',
    mode: GM_getValue('mode', 'confirm'),        // confirm | auto
    cap: GM_getValue('cap', CFG.defaultCap),
    sendMin: GM_getValue('sendMin', CFG.defaultSendMin),
    sendMax: GM_getValue('sendMax', CFG.defaultSendMax),
    threshold: GM_getValue('threshold', CFG.defaultThreshold),
    band: GM_getValue('band', 'apply'),          // apply | apply+review
    maxPages: GM_getValue('maxPages', CFG.maxPages),
    screening: null,     // 候选人资质应答（来自服务端 /health 的 screening 字段）
    busy: false,
    running: false,
    dryRun: false,
    letter: '',
    jd: '',
    doneIdx: new Set(),
    todayKey: '',
    stats: null,
  };

  const today = () => new Date().toISOString().slice(0, 10);
  const sentToday = () => (S.todayKey !== today() ? 0 : GM_getValue('sent:' + today(), 0));
  const bumpSent = () => {
    S.todayKey = today();
    GM_setValue('sent:' + today(), sentToday() + 1);
    refreshHud();
  };

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1));

  // ------------------------------------------------------------------
  // 已投递去重：这是「跨天、跨页面绝不重复投同一个岗位」的保证。
  // 存在油猴脚本自己的存储里，刷新、关浏览器都不丢。
  // ------------------------------------------------------------------
  function appliedSet() {
    const a = GM_getValue(APPLIED_KEY, []);
    return new Set(Array.isArray(a) ? a : []);
  }
  function hasApplied(id) {
    if (!id) return false;
    return appliedSet().has(id);
  }
  function markApplied(id) {
    if (!id) return;
    const s = appliedSet();
    s.add(id);
    let arr = [...s];
    if (arr.length > 800) arr = arr.slice(-500);   // 别把存储撑爆
    GM_setValue(APPLIED_KEY, arr);
  }
  function appliedCount() { return appliedSet().size; }

  // ==================================================================
  // 日志 / 提示
  // ==================================================================
  let logBox = null;
  function log(msg, kind) {
    const line = document.createElement('div');
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    line.style.cssText = 'padding:2px 0;border-bottom:1px dashed #eee;' +
      (kind === 'err' ? 'color:#c62828;' : kind === 'ok' ? 'color:#2e7d32;'
        : kind === 'skip' ? 'color:#999;' : 'color:#333;');
    if (logBox) {
      logBox.appendChild(line);
      logBox.scrollTop = logBox.scrollHeight;
      while (logBox.children.length > 300) logBox.removeChild(logBox.firstChild);
    }
    console.log('[求职助手]', msg);
  }

  function toast(msg) {
    const t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = `position:fixed;left:50%;top:18%;transform:translateX(-50%);
      background:rgba(20,20,20,.9);color:#fff;padding:10px 18px;border-radius:8px;
      font-size:14px;z-index:2147483647;pointer-events:none;max-width:70vw;`;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2200);
  }

  // ==================================================================
  // 与本地服务通信（话术引擎 + 筛选引擎）
  // ==================================================================
  function api(path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: payload ? 'POST' : 'GET',
        url: CFG.server + path,
        headers: { 'Content-Type': 'application/json' },
        data: payload ? JSON.stringify(payload) : undefined,
        timeout: 120000,
        onload: (res) => {
          let data;
          try { data = JSON.parse(res.responseText); }
          catch (e) { return reject(new Error('服务返回的不是 JSON：' + res.responseText.slice(0, 200))); }
          resolve(data);
        },
        onerror: () => reject(new Error(
          '连不上本地服务。请确认已双击 start_ai_server.bat 并保持窗口开着。')),
        ontimeout: () => reject(new Error('本地服务响应超时（>120秒）')),
      });
    });
  }

  // ==================================================================
  // 页面判断
  // ==================================================================
  function isVerifyPage() {
    if (!SITE) return false;
    const t = document.body ? document.body.innerText.slice(0, 3000).toLowerCase() : '';
    const u = location.href.toLowerCase();
    return SITE.verifyHints.some(h => t.includes(h.toLowerCase()) || u.includes(h.toLowerCase()));
  }

  function isLoggedIn() {
    if (!SITE) return true;
    return SITE.loggedInSelectors.some(s => !!document.querySelector(s));
  }

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 20;
  }

  // ==================================================================
  // 读取 JD
  // ==================================================================
  function normalize(t) { return (t || '').replace(/[\s:：]/g, ''); }

  // 中英 JD 特征词，用于给候选文本块打分。
  // 领英中国版是中文界面 —— 只认英文关键词会全盘失效（这是之前「没读到 JD」的根因）。
  const JD_ZH_WORDS = ['职位描述', '岗位职责', '任职要求', '工作职责', '职位要求', '岗位要求',
    '工作内容', '职位详情', '任职资格', '技能要求', '学历要求', '你将', '我们希望'];
  const JD_EN_WORDS = ['responsibilities', 'qualifications', 'about the job', 'job description',
    'requirements', 'what you', 'skills', 'experience'];

  function jdKeywordHits(t) {
    const s = t || '';
    const low = s.toLowerCase();
    let n = 0;
    for (const k of JD_ZH_WORDS) if (s.includes(k)) n++;
    for (const k of JD_EN_WORDS) if (low.includes(k)) n++;
    return n;
  }

  // 顶栏 / 侧栏 / 页脚里的文字不是 JD，必须排除，否则会读到一堆导航词
  function inChrome(el) {
    return !!(el && el.closest &&
      el.closest('nav,header,footer,aside,#global-nav,.global-nav,.global-footer'));
  }

  // 页面 UI 文字会被一并读进来（实测领英 JD 尾部带着「Show more Show less」），
  // 这种词发给 HR 很突兀，必须清掉。
  const JD_UI_NOISE = [
    /展开全部/g, /收起全文/g, /收起/g, /查看更多/g, /查看全部/g, /显示更多/g,
    /Show more/gi, /Show less/gi, /See more/gi, /See less/gi, /Read more/gi,
  ];

  function cleanJd(t) {
    let s = String(t || '').replace(/\r/g, '');
    for (const re of JD_UI_NOISE) s = s.replace(re, ' ');
    return s.split('\n')
      .map(l => l.replace(/[ \t]{2,}/g, ' ').trim())
      .filter(Boolean).join('\n').trim();
  }

  let lastJdPlan = '';  // 这条 JD 是哪一档兜底读到的；调试导出里会带上

  function jdFromDom() {
    if (!SITE) return '';
    lastJdPlan = '';

    // 方案 A：先找到「职位描述 / About the job」这类小标题，再往上取容器文本。
    // 标题文字比 class 名稳定得多，改版后这个办法通常还能用。
    const all = document.querySelectorAll('div,span,h1,h2,h3,h4,p,strong,b');
    for (const el of all) {
      if (el.children.length > 0) continue;
      const txt = normalize(el.textContent);
      if (!txt) continue;
      // 判定「这是个标题」而不是「正文里恰好出现这个词」：
      // 只认完全相等、或只多一个小尾巴（「职位描述：」「About the job」）的短文本。
      // ⚠ 之前写的是 n.length > 4 才允许模糊匹配 —— 而中文标题全是 4 个字
      //   （职位描述/岗位职责/任职要求/工作职责…），条件恒为假，方案 A 等于全程失效。
      const hit = SITE.jdHeadings.find(h => {
        const n = normalize(h);
        if (txt === n) return true;
        return txt.startsWith(n) && txt.length <= n.length + 12;
      });
      if (!hit) continue;
      if (!visible(el) || inChrome(el)) continue;
      let p = el.parentElement;
      for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
        if (inChrome(p)) break;
        const t = cleanJd(p.innerText);
        if (t.length >= 150 && t.length <= 12000) {
          lastJdPlan = '标题定位「' + hit + '」';
          return t;
        }
      }
    }

    // 方案 B：按选择器优先级依次试，第一个拿到像样长度的就直接返回。
    // ⚠ 不能写成「所有选择器里取最长」—— 大容器通常更长，反而更脏，
    //   实测会把「Show more / Show less」按钮文字一起吃进来。
    for (const s of SITE.jdSelectors) {
      let picked = '';
      document.querySelectorAll(s).forEach(el => {
        if (!visible(el) || inChrome(el)) return;
        const t = cleanJd(el.innerText);
        if (t.length > picked.length && t.length <= 12000) picked = t;
      });
      if (picked.length >= 150) {
        lastJdPlan = '选择器命中 ' + s;
        return picked;
      }
    }

    // 方案 C：全页面打分，挑最像 JD 的文本块（中英关键词都算）
    let fallback = '', bestScore = -1;
    document.querySelectorAll('div,section,article,main').forEach(el => {
      if (el.children.length > 30 || inChrome(el)) return;
      const t = cleanJd(el.innerText);
      if (t.length < 250 || t.length > 12000) return;
      const hits = jdKeywordHits(t);
      if (hits < 2) return;
      const score = hits * 1000 + Math.min(t.length, 5000) / 100;
      if (score > bestScore) { bestScore = score; fallback = t; }
    });
    if (fallback) { lastJdPlan = '全页打分兜底'; return fallback; }

    // 方案 D：主内容区兜底。领英的 main 里就是岗位内容，不含顶栏导航
    const m = document.querySelector('main');
    if (m) {
      const t = cleanJd(m.innerText);
      if (t.length >= 300 && t.length <= 12000 && jdKeywordHits(t) >= 1) {
        lastJdPlan = 'main 主内容区兜底';
        return t;
      }
    }
    return '';
  }

  function titleFromDom() {
    if (!SITE) return '';
    for (const s of SITE.titleSelectors) {
      const el = document.querySelector(s);
      if (el && visible(el)) {
        const t = (el.innerText || '').trim().split('\n')[0];
        if (t && t.length < 80) return t;
      }
    }
    return (document.title || '').replace(/[-_|].*$/, '').trim();
  }

  // ==================================================================
  // 岗位卡片：识别、抽取信息、本地预筛
  // ==================================================================
  function jobCards() {
    if (!SITE) return [];
    for (const s of SITE.cardSelectors || []) {
      const els = [...document.querySelectorAll(s)].filter(visible);
      if (els.length) return els;
    }
    return [];
  }

  function pickText(el, selectors) {
    for (const s of selectors || []) {
      const t = el.querySelector(s);
      if (t && (t.innerText || '').trim()) return (t.innerText || '').trim().split('\n')[0];
    }
    return '';
  }

  function jobIdOf(el) {
    const d = el.dataset || {};
    const cand = d.occludableJobId || d.jobId || d.jobid || d.lid ||
      el.getAttribute('data-occludable-job-id') || el.getAttribute('data-job-id') ||
      el.getAttribute('data-jobid');
    if (cand) return String(cand).trim();
    const a = el.querySelector('a[href*="job_detail"], a[href*="/jobs/view/"]');
    if (a) {
      const m = (a.getAttribute('href') || '').match(/job_detail\/([\w~-]+)/) ||
        (a.getAttribute('href') || '').match(/\/jobs\/view\/(\d+)/);
      if (m) return m[1];
    }
    return '';
  }

  function cardMeta(el) {
    const title = pickText(el, SITE.cardTitle) ||
      ((el.innerText || '').trim().split('\n')[0] || '').slice(0, 60);
    const company = pickText(el, SITE.cardCompany);
    const meta = pickText(el, SITE.cardMeta);
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 400);
    return { el, id: jobIdOf(el), title, company, meta, text };
  }

  // 已经完全投过 / 平台已标记投递的卡片，直接不看
  const APPLIED_MARKS = ['applied', '已申请', '已投递', '已沟通', 'application sent'];

  function looksApplied(meta) {
    const t = (meta.text || '').toLowerCase();
    return APPLIED_MARKS.some(m => t.includes(m.toLowerCase()));
  }

  // 本地预筛：只做「一眼就能看出来不投」的排除，省掉一次点击 + 一次 JD 解析。
  // ⚠ 这份表必须和 matcher.py 的 EXCLUDE_HARD 保持同源，但**只匹配标题**。
  //    曾经拿卡片全文匹配，结果 BOSS 福利标签里的「股票期权」「培训」
  //    把正经研发岗判成了证券/培训销售。
  const LOCAL_EXCLUDE = [
    '销售', '保险', '房产', '中介', '招生', '课程顾问', '导购', '招商', '地推',
    '贷款', '理财', '催收', '客户经理', '医药代表', '客户代表', '业务代表',
    '服务员', '客服', '司机', '普工', '外卖', '骑手', '主播', '直播', '保安',
    '保洁', '厨师', '月嫂', '美发', '快递', '分拣', '仓管', '收银', '前台',
    '文员', '行政', '人事', '招聘专员', '会计', '出纳', '法务', '审计',
    '电商运营', '新媒体运营', '文案策划', '美工', '教师', '幼教', '助教',
    '证券', '期货', '护士', '医师', '药师', '检验技师', '注册专员',
    'sales representative', 'account executive', 'insurance agent',
    'recruiter', 'talent acquisition', 'customer service', 'cashier', 'nurse',
  ];

  function localPreFilter(meta) {
    const title = (meta.title || '').toLowerCase();
    const hit = LOCAL_EXCLUDE.find(w => title.includes(w.toLowerCase()));
    if (hit) return { pass: false, reason: '标题含排除词「' + hit + '」' };
    if (SITE.key === 'linkedin' && looksApplied(meta)) {
      return { pass: false, reason: 'LinkedIn 已标记「已申请」' };
    }
    return { pass: true, reason: '' };
  }

  // ==================================================================
  // 填入 / 发送
  // ==================================================================
  function findByText(kw, root) {
    const scope = root || document;
    for (const el of scope.querySelectorAll('a,button,span,div')) {
      if (el.children.length > 2) continue;
      if ((el.innerText || '').replace(/\s/g, '') === kw && visible(el)) return el;
    }
    return null;
  }

  function findBySelector(list, root) {
    for (const s of list || []) {
      const scope = root || document;
      const els = scope.querySelectorAll(s);
      for (const el of els) if (visible(el)) return el;
    }
    return null;
  }

  function findAllBySelector(list, root) {
    const out = [];
    for (const s of list || []) {
      const scope = root || document;
      scope.querySelectorAll(s).forEach(el => { if (visible(el)) out.push(el); });
    }
    return out;
  }

  function findGreetButton() {
    if (!SITE || !(SITE.greetSelectors || []).length) return null;
    return findBySelector(SITE.greetSelectors) ||
      SITE.greetTexts.map(t => findByText(t)).find(Boolean) || null;
  }

  function findChatInput(root) {
    if (!SITE) return null;
    for (const s of SITE.inputSelectors) {
      const els = (root || document).querySelectorAll(s);
      for (const el of els) {
        if (!visible(el)) continue;
        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.isContentEditable) return el;
      }
    }
    return null;
  }

  function fillInput(el, text) {
    el.focus();
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      // React 受控组件必须走原生 setter，否则赋值不生效
      const proto = el.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
    } else {
      // contenteditable 走 execCommand 最稳，React 能收到 input 事件
      el.textContent = text;
      try {
        const sel = window.getSelection();
        const r = document.createRange();
        r.selectNodeContents(el);
        sel.removeAllRanges(); sel.addRange(r);
        document.execCommand('insertText', false, text);
      } catch (e) { /* 用 textContent 的兜底结果 */ }
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
  }

  function findSendButton(root) {
    if (!SITE) return null;
    const bySel = findBySelector(SITE.sendSelectors, root);
    if (bySel) return bySel;
    for (const t of SITE.sendTexts) {
      const b = findByText(t, root);
      if (b) return b;
    }
    return null;
  }

  /**
   * 把话术送进目标输入框。
   * autoSend=true 时尝试点发送；BOSS 上如果没有现成输入框，会先点「立即沟通」。
   */
  async function deliver(letter, autoSend) {
    let input = findChatInput();

    // BOSS：输入框不存在就先点「立即沟通」把它叫出来
    if (!input && (SITE.greetSelectors || []).length) {
      const btn = findGreetButton();
      if (!btn) throw new Error('找不到「立即沟通」按钮（页面结构可能变了，点调试导出）');
      btn.click();
      log('已点「立即沟通」，等待聊天框…');
      for (let i = 0; i < 30 && !input; i++) {
        await sleep(400);
        input = findChatInput();
      }
    }

    if (!input) {
      throw new Error(SITE.key === 'linkedin'
        ? '没找到输入框。LinkedIn 请先把 Easy Apply 弹窗、加好友备注框或聊天框打开，再点①。'
        : '点了沟通但没等到输入框');
    }

    fillInput(input, letter);
    log('话术已填入输入框', 'ok');

    if (!autoSend) {
      input.focus();
      return 'filled';
    }

    // LinkedIn 的 Easy Apply 是分步表单，自动提交风险大 —— 只填不交
    if (SITE.key === 'linkedin' && !findSendButton()) {
      input.focus();
      log('LinkedIn 这个表单没找到直接发送按钮（多为 Easy Apply 分步表单），已填好，请你确认后提交。', 'ok');
      return 'filled';
    }

    await sleep(600);
    const sendBtn = findSendButton();
    if (sendBtn) {
      sendBtn.click();
    } else {
      ['keydown', 'keyup'].forEach(ev =>
        input.dispatchEvent(new KeyboardEvent(ev,
          { bubbles: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 })));
    }
    await sleep(900);
    bumpSent();
    log('已发送', 'ok');
    return 'sent';
  }

  // ==================================================================
  // LinkedIn Easy Apply：自动走完分步表单
  //
  // 说明：LinkedIn 的 Easy Apply 是「Contact info → Resume → Additional
  // questions → Review → Submit」多步弹窗。绝大多数步骤只要点「下一步」，
  // 只有少数岗位会在 Additional questions 里问签证 / 工作许可这类问题。
  //
  // 对这类问题本脚本**故意不自动作答**，而是把这条岗位退回人工 ——
  // 编一个答案（比如谎报有工作许可）代价远大于少投一个岗位。
  // ==================================================================
  const EA = {
    modal: ['.jobs-easy-apply-modal', 'div[role="dialog"][data-test-modal]',
      '.artdeco-modal[role="dialog"]', '[role="dialog"]'],
    next: ['button[aria-label="Continue to next step"]', 'button[aria-label="继续下一步"]',
      'button[aria-label="Next"]', 'button[aria-label="下一步"]',
      'footer button.artdeco-button--primary'],
    review: ['button[aria-label="Review your application"]', 'button[aria-label="检查申请"]',
      'button[aria-label="Review"]'],
    submit: ['button[aria-label="Submit application"]', 'button[aria-label="提交申请"]',
      'button[aria-label="Submit"]', 'button[aria-label="提交"]'],
    dismiss: ['button[aria-label="Dismiss"]', 'button[aria-label="关闭"]',
      'button[aria-label="取消"]', '.artdeco-modal__dismiss'],
    discard: ['button[aria-label="Discard"]', 'button[data-control-name="discard_application_confirm_btn"]',
      'button[aria-label="放弃"]'],
    external: ['button[aria-label*="company website"]', 'button[aria-label*="公司网站"]'],
  };

  // 这些是需要「如实回答」的问题 —— 脚本不替你答，交回人工
  const MANUAL_QUESTIONS = [/sponsor/i, /visa/i, /authorized to work/i,
    /work permit/i, /legally (able|authorized)/i, /citizen/i, /clearance/i,
    /签证/, /工作许可/, /合法工作/, /国籍/];

  function eaModal() { return findBySelector(EA.modal); }

  function clickIfFound(list, root) {
    const b = findBySelector(list, root);
    if (b && !b.disabled && b.getAttribute('aria-disabled') !== 'true') { b.click(); return true; }
    return false;
  }

  // 找出弹窗里「必填但没填」的单选/下拉/文本，判断能不能自动处理
  function blockingFields(modal) {
    const out = [];
    modal.querySelectorAll('fieldset').forEach(fs => {
      const required = fs.querySelector('[aria-required="true"], [required]');
      if (!required) return;
      const radios = [...fs.querySelectorAll('input[type="radio"]')];
      if (radios.length) {
        if (radios.some(r => r.checked)) return;
        const legend = fs.querySelector('legend');
        out.push(((legend ? legend.innerText : fs.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 160));
      } else {
        // 没有单选、但有必填文本域（如「描述你的签证状态」）→ 也记下来
        const inp = fs.querySelector('textarea, input[type="text"]');
        if (inp && !(inp.value || '').trim()) {
          const legend = fs.querySelector('legend');
          out.push(((legend ? legend.innerText : fs.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 160));
        }
      }
    });
    modal.querySelectorAll('select[required], select[aria-required="true"]').forEach(sel => {
      if (sel.value) return;
      const lab = sel.closest('label') || sel.parentElement;
      out.push(((lab && lab.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 160));
    });
    return out.filter(Boolean);
  }

  // 把 .env 里候选人如实填写的资质，照实勾到 Easy Apply 的筛查题上。
  // 只在 AUTO_ANSWER_SCREENING 开、问题能对上已知类别、且能找到匹配选项时才作答；
  // 否则一律不碰 —— 交由下面的逻辑退回人工，绝不瞎填、绝不编答案。
  function answerScreening(modal) {
    const scr = S.screening || {};
    const answered = [];
    const failed = [];
    if (!scr.auto_answer) return { answered, failed };

    const wantSponsor = scr.require_sponsorship === true;   // 需要赞助 → Yes
    const authorized = scr.authorized === true;             // 已有授权 → Yes（否则 No，但注明需赞助）
    const nat = (scr.nationality || '').trim();

    // 在某个标签串里挑出最符合「想要」的那一项
    const pick = (labels, wantYes) => {
      // 先找明确命中「wantYes 方向的词」的选项
      const hit = labels.find(l =>
        wantYes
          ? /(require|will require|need|need sponsorship|yes|需要|是|需)/i.test(l.text) && !/(no|not|不|否)/i.test(l.text)
          : /(do not|not require|no sponsorship|no,? but|will not|no|不需要|否|不)/i.test(l.text));
      if (hit) { hit.r.checked = true; hit.r.dispatchEvent(new Event('change', { bubbles: true })); return true; }
      return false;
    };

    modal.querySelectorAll('fieldset').forEach(fs => {
      const required = fs.querySelector('[aria-required="true"], [required]');
      if (!required) return;
      const radios = [...fs.querySelectorAll('input[type="radio"]')];
      const q = ((fs.querySelector('legend') ? fs.querySelector('legend').innerText : fs.innerText) || '')
        .replace(/\s+/g, ' ').trim();
      const labels = radios.map(r => {
        const lab = r.closest('label');
        return { r, text: ((lab ? lab.innerText : '') || '').replace(/\s+/g, ' ').trim() };
      });

      let handled = false;

      // 1) 签证 / 工作许可赞助 类（"require sponsorship?" 等）
      if (/sponsor|visa|work permit|工作许可|签证/i.test(q) && !/citizen|国籍/.test(q)) {
        handled = pick(labels, wantSponsor);
        if (handled) answered.push('sponsorship→' + (wantSponsor ? 'Yes' : 'No'));

      // 2) 是否已拥有目标国合法工作授权（"authorized to work?" 等）
      } else if (/authorized to work|legally (able|authorized)|合法工作/i.test(q)) {
        if (authorized) {
          handled = pick(labels, true);
        } else {
          // 优先「No，但需要赞助」那一项；没有就退而选普通 No
          handled = pick(labels, false) ||
            labels.some(l => /(require|sponsor|需要|需)/i.test(l.text) && /(no|not|不)/i.test(l.text)) &&
            (() => { const h = labels.find(l => /(require|sponsor|需要|需)/i.test(l.text) && /(no|not|不)/i.test(l.text)); h.r.checked = true; h.r.dispatchEvent(new Event('change', { bubbles: true })); return true; })();
        }
        if (handled) answered.push('authorized→' + (authorized ? 'Yes' : 'No'));

      // 3) 公民身份（"are you a citizen of X?"）
      } else if (/citizen/i.test(q)) {
        handled = pick(labels, authorized);
        if (handled) answered.push('citizen→' + (authorized ? 'Yes' : 'No'));

      // 4) 国籍（填文本框 / 下拉）
      } else if (/nationality|国籍/i.test(q)) {
        const inp = fs.querySelector('input[type="text"], input:not([type]), select');
        if (inp && nat) {
          inp.value = nat;
          inp.dispatchEvent(new Event('change', { bubbles: true }));
          answered.push('nationality=' + nat);
          handled = true;
        }
      }

      if (!handled) failed.push(q.slice(0, 60));
    });

    // 必填下拉也尝试（国籍等）
    modal.querySelectorAll('select[required], select[aria-required="true"]').forEach(sel => {
      if (sel.value) return;
      const q = ((sel.closest('label') || sel.parentElement || sel).innerText || '').replace(/\s+/g, ' ').trim();
      if (/nationality|国籍/i.test(q) && nat) {
        for (const opt of sel.options) {
          if (new RegExp(nat, 'i').test(opt.text) || new RegExp(nat, 'i').test(opt.value)) {
            sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true }));
            answered.push('nationality=' + nat); return;
          }
        }
        failed.push(q.slice(0, 60));
      } else {
        failed.push(q.slice(0, 60));
      }
    });

    return { answered, failed };
  }

  async function closeModalSafely() {
    const modal = eaModal();
    if (!modal) return;
    clickIfFound(EA.discard, modal) || clickIfFound(EA.dismiss, modal);
    await sleep(800);
    const m2 = eaModal();
    if (m2) { clickIfFound(EA.discard, m2) || clickIfFound(EA.dismiss, m2); await sleep(600); }
  }

  async function easyApply(letter) {
    const btn = findBySelector(SITE.applySelectors);
    if (!btn) throw new Error('页面上找不到「申请 / Easy Apply」按钮');

    // 跳转公司官网的申请 → 脚本管不了，退回人工
    const label = (btn.getAttribute('aria-label') || btn.innerText || '');
    if (findBySelector(EA.external) || /company website|公司网站|外部/i.test(label)) {
      return { status: 'manual', reason: '该岗位跳转公司官网申请，需人工完成' };
    }

    btn.click();
    await sleep(1800);

    let modal = eaModal();
    if (!modal) return { status: 'manual', reason: '点了申请但没弹出表单（可能是外部申请）' };

    for (let step = 1; step <= 10; step++) {
      if (isVerifyPage()) { await closeModalSafely(); return { status: 'stop', reason: '撞上安全验证' }; }
      modal = eaModal();
      if (!modal) break;

      // 1) 填充弹窗里所有空的文本域（求职信 / 补充说明）。
      //    ⚠ 但「签证状态 / 工作许可」这类筛查文本框绝不填 —— 一旦填了，
      //       下面的 blockingFields 会以为答过了，就把本该退回人工的题硬投出去。
      //       这类题保持空，answerScreening 处理不了就原样退回人工。
      const screeningText = (el) => {
        const fs = el.closest('fieldset');
        const leg = fs && fs.querySelector('legend') ? fs.querySelector('legend').innerText : '';
        const lab = el.closest('label');
        const s = (leg + ' ' + (lab ? lab.innerText : '') + ' ' + (el.getAttribute('aria-label') || '')
          + ' ' + (el.name || '') + ' ' + (el.id || '')).replace(/\s+/g, ' ');
        return MANUAL_QUESTIONS.some(rx => rx.test(s));
      };
      const areas = findAllBySelector(['textarea'], modal)
        .filter(t => !(t.value || '').trim())
        .filter(t => !screeningText(t));
      for (const t of areas) { fillInput(t, letter); }
      if (areas.length) log(`  · 已填入 ${areas.length} 个文本框`);

      // 2) 必填但没填的问题 → 先试着用 .env 里的真实资质如实勾选；
      //    勾不上的（或 auto_answer 关的）才退回人工，绝不编答案。
      const blocking = blockingFields(modal);
      if (blocking.length) {
        const { answered, failed } = answerScreening(modal);
        if (answered.length) log('  · 已如实填报资质题：' + answered.join('；'));
        if (failed.length) {
          const risky = failed.find(q => MANUAL_QUESTIONS.some(rx => rx.test(q)));
          await closeModalSafely();
          return {
            status: 'manual',
            reason: (risky ? '签证/工作许可类问题需你亲自回答：' : '必填题脚本不敢代答：')
              + failed[0].slice(0, 60),
          };
        }
        await sleep(500);   // 让勾选后的表单状态稳定
      }

      // 3) 提交
      if (clickIfFound(EA.submit, modal)) {
        await sleep(2200);
        // ⚠ 必须在这里记一笔「今天投了几条」。
        //   BOSS 走的是 deliver()，那里会 bumpSent；而 LinkedIn 走的是这条 Easy Apply
        //   路径，如果忘了记账，每日上限就永远不会触发 —— 等于安全阀失效。
        bumpSent();
        if (!eaModal()) return { status: 'sent', reason: 'Easy Apply 已提交' };
        return { status: 'sent', reason: '已点提交（弹窗未关，请瞄一眼）' };
      }

      // 4) 下一步 / 检查
      const advanced = clickIfFound(EA.next, modal) || clickIfFound(EA.review, modal);
      if (!advanced) {
        await closeModalSafely();
        return { status: 'manual', reason: `第 ${step} 步找不到「下一步/提交」按钮，需人工` };
      }
      log(`  · Easy Apply 第 ${step} 步 → 下一步`);
      await sleep(1600);
    }

    await closeModalSafely();
    return { status: 'manual', reason: 'Easy Apply 步骤超过 10 步，已放弃，需人工' };
  }

  // ==================================================================
  // 核心：处理当前岗位（读 JD → 问筛选引擎 → 写话术 → 投）
  // ==================================================================
  async function doCurrent(autoSend, opts) {
    opts = opts || {};
    if (S.busy) return false;
    if (isVerifyPage()) {
      log('⚠ 检测到安全验证页，已停下。请手动过验证后刷新。', 'err');
      S.running = false; refreshHud();
      return false;
    }

    S.busy = true; refreshHud();
    try {
      const jd = S.jd || jdFromDom();
      if (!jd || jd.length < 60) {
        throw new Error('没读到岗位 JD。请先点开一个岗位详情，或把 JD 粘到面板的文本框里。');
      }
      S.jd = jd;
      const title = titleFromDom() || '(未识别)';
      log(`岗位：${title} ｜ JD ${jd.length} 字`);

      const res = await api('/letter', { jd, scene: S.scene });
      if (!res.ok) throw new Error(res.error || '生成失败');

      S.letter = res.letter;
      letterBox.value = res.letter;
      updateCounter();
      log(`话术已生成：${res.chars} 字 ｜ 角度 ${res.angle} ｜ ${res.seconds}s`, 'ok');

      const r = await deliver(res.letter, !!autoSend);
      if (r === 'filled') toast('话术已填好，确认后发送');
      return true;
    } catch (e) {
      log('✗ ' + e.message, 'err');
      toast('出错：' + e.message);
      return false;
    } finally {
      S.busy = false; refreshHud();
    }
  }

  // 等 JD 渲染出来（点击卡片后右侧详情是异步加载的）
  async function waitForJd(prevJd, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 15000);
    while (Date.now() < deadline) {
      await sleep(450);
      const jd = jdFromDom();
      if (jd && jd.length >= 150 && jd !== prevJd) return jd;
    }
    return '';
  }

  /**
   * 点开一张岗位卡片，等 JD 出来。
   *
   * 为什么要试多个点击目标：两个站点的卡片都有「点击容器」和「卡片主体」两层，
   * 而且改版时挂 click 监听的层会变。按下述顺序试，哪个能出 JD 就用哪个：
   *   ① li 本身  ② 里面的 .job-card-container 之类的卡片主体  ③ 其它 class 含 job-card 的块
   * 特意**不去点里面的 <a>** —— 那个 href 指向详情 URL，点下去可能整页跳走，
   * 列表页一离开，后面所有卡片就都处理不了了。
   */
  async function openCard(card, prevJd) {
    const targets = [
      card,
      card.querySelector('.job-card-container, .job-card-list, .job-card-box, .job-card-wrapper'),
      card.querySelector('[class*="job-card"]'),
    ].filter(Boolean);

    for (const t of targets) {
      if (!S.running) return '';
      try { t.scrollIntoView({ block: 'center' }); } catch (e) { /* ignore */ }
      await sleep(rand(250, 700));
      try { t.click(); } catch (e) { /* ignore */ }
      const jd = await waitForJd(prevJd, 7000);
      if (jd) return jd;
    }
    return '';
  }

  // ==================================================================
  // 自动流水线：筛选 + 投递，一把跑完
  //
  //   dryRun = true  → 只筛选，不生成话术、不投递（先看它打算投哪些）
  //   dryRun = false → 匹配上的直接生成话术并投出去
  // ==================================================================
  function newStats() { return { scanned: 0, skipped: 0, review: 0, applied: 0, manual: 0, failed: 0 }; }

  function shouldApply(decision) {
    if (decision.verdict === 'apply') return true;
    return S.band === 'apply+review' && decision.verdict === 'review';
  }

  async function autoRun(dryRun) {
    if (S.running) { S.running = false; log('已请求停止，等当前这条走完。'); refreshHud(); return; }

    S.running = true;
    S.dryRun = !!dryRun;
    S.stats = newStats();
    S.doneIdx = new Set();
    refreshHud();

    log(S.dryRun
      ? '=== 演练模式：只筛选，不投递 ==='
      : '=== 自动投递开始：筛选通过的直接投 ===');

    try {
      // 单岗位页（/jobs/view/xxx 或 BOSS 详情页）：就直接处理这一个
      if (!SITE.listRe.test(location.pathname)) {
        log('当前不是岗位列表页，按「单个岗位」处理');
        const ok = await doCurrent(!S.dryRun, { single: true });
        if (ok) S.stats.applied++;
        return;
      }

      let page = 1;
      while (S.running && page <= S.maxPages) {
        const cards = jobCards();
        if (!cards.length) {
          log('当前页找不到岗位卡片。请在招聘平台的岗位搜索结果页使用。', 'err');
          break;
        }
        log(`—— 第 ${page} 页，共 ${cards.length} 个岗位卡片 ——`);

        for (let i = 0; i < cards.length; i++) {
          if (!S.running) break;
          if (isVerifyPage()) { log('⚠ 撞上安全验证，已停止。', 'err'); S.running = false; break; }
          if (sentToday() >= S.cap) {
            log(`已达今日上限 ${S.cap} 条，停止。`, 'err'); S.running = false; break;
          }

          const card = jobCards()[i];     // 每轮重新取，SPA 会重渲染
          if (!card) continue;
          let meta = cardMeta(card);
          const key = meta.id || ('t:' + (meta.title || '').slice(0, 40));
          if (S.doneIdx.has(key)) continue;
          S.doneIdx.add(key);
          S.stats.scanned++;
          const tag = `[${S.stats.scanned}] ${(meta.title || '(无标题)').slice(0, 34)}`;

          // ---- 闸门 1：本地预筛（不点开，零成本）----
          const pre = localPreFilter(meta);
          if (!pre.pass) {
            S.stats.skipped++;
            log(`${tag} → 跳过（${pre.reason}）`, 'skip');
            continue;
          }
          // ---- 闸门 2：跨天去重 ----
          if (meta.id && hasApplied(meta.id)) {
            S.stats.skipped++;
            log(`${tag} → 跳过（之前已经投过）`, 'skip');
            continue;
          }

          // ---- 点开读 JD ----
          const prevJd = jdFromDom();
          const jd = await openCard(card, prevJd);
          if (!jd) {
            S.stats.failed++;
            log(`${tag} → 读不到 JD，跳过`, 'err');
            if (!SITE.listRe.test(location.pathname)) { history.back(); await sleep(2000); }
            continue;
          }
          S.jd = jd;
          if (!meta.title) meta.title = titleFromDom();

          // ---- 闸门 3：筛选引擎判定 ----
          let decision;
          try {
            decision = await api('/match', {
              title: meta.title, company: meta.company, jd,
              card_text: meta.text, site: SITE.key,
              threshold: S.threshold, use_llm: true,
            });
          } catch (e) {
            S.stats.failed++;
            log(`${tag} → 筛选服务出错：${e.message}`, 'err');
            continue;
          }

          log(`${tag} → ${decision.score} 分 ${decision.verdict.toUpperCase()}`
            + (decision.llm ? '（含模型复核）' : ''), decision.verdict === 'apply' ? 'ok' : 'skip');
          log(`    ${decision.reason_line}`, 'skip');

          if (decision.verdict === 'review') S.stats.review++;

          // ---- 归档：投没投都记一笔 ----
          const rec = {
            site: SITE.key, job_id: meta.id, title: meta.title, company: meta.company,
            url: location.href, score: decision.score, verdict: decision.verdict,
            scene: S.scene,
          };

          if (!shouldApply(decision)) {
            S.stats.skipped++;
            api('/record', { ...rec, status: 'skip', reason: decision.reason_line }).catch(() => {});
            const wait = rand(800, 2200);   // 跳过的岗位不用等太久，但也别 0 延迟
            for (let w = 0; w < wait / 200 && S.running; w++) await sleep(200);
            continue;
          }

          if (S.dryRun) {
            log(`    ↳ 演练：这个会投（${decision.score} 分）`, 'ok');
            api('/record', { ...rec, status: 'dry-run', reason: decision.reason_line }).catch(() => {});
            continue;
          }

          // ---- 生成话术 ----
          let letter;
          try {
            const res = await api('/letter', { jd, scene: S.scene });
            if (!res.ok) throw new Error(res.error || '生成失败');
            letter = res.letter;
            S.letter = letter; letterBox.value = letter; updateCounter();
            log(`    话术 ${res.chars} 字 ｜ 角度 ${res.angle}`, 'ok');
          } catch (e) {
            S.stats.failed++;
            log(`    话术生成失败：${e.message}`, 'err');
            continue;
          }

          // ---- 投 ----
          let status = 'sent', reason = '';
          try {
            if (SITE.key === 'linkedin') {
              const r = await easyApply(letter);
              status = r.status; reason = r.reason || '';
              if (status === 'sent') log(`    ↳ ${reason}`, 'ok');
              else if (status === 'manual') { S.stats.manual++; log(`    ↳ 需人工：${reason}`, 'err'); }
              else if (status === 'stop') { log(`    ↳ ${reason}`, 'err'); S.running = false; }
            } else {
              const r = await deliver(letter, true);
              status = r === 'sent' ? 'sent' : 'filled';
            }
          } catch (e) {
            status = 'failed'; reason = e.message;
            S.stats.failed++;
            log(`    投递失败：${e.message}`, 'err');
          }

          if (status === 'sent') {
            S.stats.applied++;
            if (meta.id) markApplied(meta.id);
            log(`    ✓ 已投递（今日 ${sentToday()}/${S.cap}）`, 'ok');
          }
          api('/record', { ...rec, status, reason, letter }).catch(() => {});

          // ---- 回到列表页 ----
          if (!SITE.listRe.test(location.pathname)) {
            log('已跳转，返回岗位列表…');
            history.back();
            await sleep(2500);
          }

          // ---- 随机间隔：投出去的那几条才需要慢慢来 ----
          if (status === 'sent') {
            const wait = rand(S.sendMin, S.sendMax);
            log(`随机等待 ${wait} 秒（避免固定节奏被识别）`);
            for (let w = 0; w < wait && S.running; w++) {
              await sleep(1000);
              if (isVerifyPage()) { log('⚠ 等待期间撞上安全验证，已停止。', 'err'); S.running = false; break; }
            }
          } else {
            await sleep(rand(1500, 3500));
          }
        }

        // ---- 翻页 ----
        if (!S.running || S.dryRun) break;
        if (sentToday() >= S.cap) break;
        if (!(await gotoNextPage(page))) break;
        page++;
      }
    } catch (e) {
      log('自动流程异常终止：' + e.message, 'err');
    } finally {
      S.running = false;
      refreshHud();
      const s = S.stats;
      const sum = `本次：扫 ${s.scanned} ｜ 投 ${s.applied} ｜ 跳过 ${s.skipped} `
        + `｜ 待人工 ${s.manual} ｜ 失败 ${s.failed}`;
      log((S.dryRun ? '演练结束。' : '自动投递结束。') + sum, 'ok');
      if (!S.dryRun) {
        GM_setClipboard(sum + `\n今日累计已投 ${sentToday()} 条`, 'text');
      }
    }
  }

  // LinkedIn / BOSS 列表翻页
  async function gotoNextPage(curPage) {
    const next = curPage + 1;
    const btns = [...document.querySelectorAll('button')].filter(visible);
    const btn = btns.find(b => {
      const l = b.getAttribute('aria-label') || b.innerText || '';
      return l.trim() === String(next) || l.includes(`第 ${next} 页`) || l.includes(`Page ${next}`);
    });
    if (!btn) return false;
    const first = (jobCards()[0] || {}).innerText || '';
    btn.scrollIntoView({ block: 'center' });
    btn.click();
    log(`翻到第 ${next} 页…`);
    for (let i = 0; i < 24; i++) {
      await sleep(500);
      const cur = (jobCards()[0] || {}).innerText || '';
      if (cur && cur !== first) { S.doneIdx = new Set(); return true; }
    }
    log('翻页没等到新内容，停止。', 'err');
    return false;
  }

  // ==================================================================
  // 调试导出
  // ==================================================================
  function debugDump() {
    const jd = jdFromDom() || '';
    const out = {
      site: SITE ? SITE.key : 'unknown',
      url: location.href,
      pageTitle: document.title,
      scene: S.scene,
      threshold: S.threshold,
      jdLength: jd.length,
      jdPlan: lastJdPlan || '(没读到)',
      jdHead: jd.slice(0, 400),
      jdTail: jd.slice(-200),
      titleFound: titleFromDom(),
      greetButton: (() => { const b = findGreetButton(); return b ? { cls: b.className, text: b.innerText } : null; })(),
      applyButton: (() => {
        const b = findBySelector((SITE && SITE.applySelectors) || []);
        return b ? { text: b.innerText, aria: b.getAttribute('aria-label') } : null;
      })(),
      chatInput: (() => { const i = findChatInput(); return i ? { tag: i.tagName, cls: i.className, id: i.id } : null; })(),
      sendButton: (() => { const b = findSendButton(); return b ? { text: b.innerText, aria: b.getAttribute('aria-label') } : null; })(),
      // —— 岗位卡片：自动模式全靠它，读不到就什么都筛不了 ——
      jobCards: jobCards().length,
      jobCardSample: jobCards().slice(0, 6).map(el => ({
        cls: String(el.className).slice(0, 90),
        id: jobIdOf(el),
        title: pickText(el, SITE.cardTitle),
        company: pickText(el, SITE.cardCompany),
        meta: pickText(el, SITE.cardMeta),
        head: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 120),
      })),
      appliedStore: appliedCount(),
      // —— 诊断用：万一还是读不到，这几个字段能一次定位原因 ——
      hasMain: !!document.querySelector('main'),
      mainLen: (document.querySelector('main') ? document.querySelector('main').innerText : '')
        .trim().length,
      iframes: document.querySelectorAll('iframe').length,
      bodyLen: (document.body.innerText || '').length,
      jdHitsInBody: jdKeywordHits(document.body.innerText || ''),
      bigBlocks: [...document.querySelectorAll('div,section,article,main')]
        .map(el => ({ cls: String(el.className).slice(0, 90), id: el.id,
          len: (el.innerText || '').trim().length, kids: el.children.length }))
        .filter(o => o.len > 400 && o.len < 15000)
        .sort((a, b) => b.len - a.len)
        .slice(0, 12),
      candidates: [...document.querySelectorAll('div,section,article')]
        .filter(el => {
          const t = (el.innerText || '').trim();
          return t.length > 200 && t.length < 7000 && el.children.length <= 14;
        })
        .slice(0, 15)
        .map(el => ({ cls: String(el.className).slice(0, 120), id: el.id, len: el.innerText.trim().length })),
    };
    const txt = JSON.stringify(out, null, 2);
    GM_setClipboard(txt, 'text');
    log('调试信息已复制到剪贴板，粘给我即可', 'ok');
    console.log(txt);
  }

  // ==================================================================
  // 面板 UI
  // ==================================================================
  let panel, letterBox, logBoxEl, hud, statusDot, counterEl, statsEl;

  function css(el, s) { el.style.cssText = s; return el; }

  function btn(label, bg) {
    const b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = `padding:6px 10px;margin:0 0 6px 0;border:1px solid ${bg || '#d0d0d0'};
      border-radius:6px;background:#fff;color:#222;font-size:13px;cursor:pointer;`;
    b.onmouseenter = () => { b.style.background = '#f2f7ff'; };
    b.onmouseleave = () => { b.style.background = ''; };
    b.onmousedown = (e) => e.stopPropagation();
    return b;
  }

  function sceneLabel(k) { return (CFG.scenes[k] || {}).label || k; }

  function updateCounter() {
    if (!counterEl) return;
    const t = (letterBox.value || '');
    const conf = CFG.scenes[S.scene] || {};
    const n = conf.words ? t.trim().split(/\s+/).filter(Boolean).length : t.length;
    const unit = conf.words ? '词' : '字符';
    let msg = `${n} ${unit}`;
    let color = '#888';
    if (conf.hard && n > conf.hard) {
      msg += `　⚠ 超过硬上限 ${conf.hard}，发不出去，请手动删减或点「重新生成」`;
      color = '#c62828';
    } else if (conf.limit && n > conf.limit * 1.15) {
      msg += `　（目标 ${conf.limit} 以内）`;
      color = '#ef6c00';
    } else {
      msg += `　（目标 ${conf.limit} 以内）`;
    }
    counterEl.textContent = msg;
    counterEl.style.color = color;
  }

  function buildPanel() {
    panel = document.createElement('div');
    // ⚠ 必须给真面板设这个 id：boot() 靠它防重复挂载，Console 里也靠它自查。
    //   之前只在 boot() 里给一个临时 div 设了这个 id 又立刻删掉 —— 结果守卫恒为假，
    //   而且 Console 里 getElementById 永远返回 null（面板明明在页面上）。
    panel.id = 'ai-job-helper-panel';
    css(panel, `position:fixed;top:80px;right:16px;width:340px;max-height:86vh;overflow-y:auto;
      background:#fff;border:1px solid #d9d9d9;border-radius:10px;
      box-shadow:0 6px 24px rgba(0,0,0,.16);z-index:2147483646;
      font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#222;`);

    const head = css(document.createElement('div'),
      'padding:9px 12px;background:#f6f8fb;border-bottom:1px solid #e6e6e6;' +
      'border-radius:10px 10px 0 0;cursor:move;display:flex;align-items:center;gap:8px;');
    statusDot = css(document.createElement('span'),
      'width:9px;height:9px;border-radius:50%;background:#bbb;flex:0 0 auto;');
    const htxt = css(document.createElement('strong'), 'flex:1;font-size:13px;');
    htxt.textContent = 'AI 求职助手 · ' + (SITE ? SITE.name : '未知站点');
    const collapse = css(document.createElement('span'), 'cursor:pointer;color:#888;font-size:16px;');
    collapse.textContent = '−';
    head.append(statusDot, htxt, collapse);

    const body = document.createElement('div');
    css(body, 'padding:10px 12px 14px;');

    (function makeDraggable() {
      let sx, sy, ox, oy, dragging = false;
      head.addEventListener('mousedown', e => {
        dragging = true; sx = e.clientX; sy = e.clientY;
        const r = panel.getBoundingClientRect(); ox = r.left; oy = r.top;
        e.preventDefault();
      });
      window.addEventListener('mousemove', e => {
        if (!dragging) return;
        panel.style.left = (ox + e.clientX - sx) + 'px';
        panel.style.top = (oy + e.clientY - sy) + 'px';
        panel.style.right = 'auto';
      });
      window.addEventListener('mouseup', () => dragging = false);
    })();

    let collapsed = false;
    collapse.onclick = () => {
      collapsed = !collapsed;
      body.style.display = collapsed ? 'none' : 'block';
      collapse.textContent = collapsed ? '+' : '−';
    };

    hud = css(document.createElement('div'),
      'font-size:12px;color:#666;margin-bottom:8px;padding:6px 8px;background:#fafafa;border-radius:6px;');

    // ---------------- 自动档位 ----------------
    const autoTitle = css(document.createElement('div'),
      'font-size:12px;font-weight:600;color:#333;margin-bottom:4px;');
    autoTitle.textContent = '自动筛选 + 投递';

    const autoBtn = btn('▶ 一键自动：筛完就投');
    autoBtn.style.cssText += 'width:100%;background:#e8f5e9;border-color:#66bb6a;font-weight:600;margin-bottom:4px;';
    autoBtn.onclick = () => autoRun(false);

    const dryBtn = btn('◐ 先演练一遍（只筛不投）');
    dryBtn.style.cssText += 'width:100%;background:#fff8e1;margin-bottom:4px;';
    dryBtn.onclick = () => autoRun(true);

    const stopBtn = btn('■ 停止');
    stopBtn.style.cssText += 'width:100%;background:#ffebee;margin-bottom:6px;';
    stopBtn.onclick = () => { S.running = false; S.busy = false; log('已请求停止…'); refreshHud(); };

    statsEl = css(document.createElement('div'),
      'font-size:11px;color:#555;background:#f6f8fb;border-radius:6px;padding:5px 8px;margin-bottom:8px;');
    statsEl.id = 'ai-job-stats';   // 加 id 方便自查（和 Console 调试）

    // ---------------- 单条手动档位 ----------------
    const manualTitle = css(document.createElement('div'),
      'font-size:12px;font-weight:600;color:#333;margin:6px 0 4px;');
    manualTitle.textContent = '单条手动（本页这个岗位）';

    // 场景
    const sceneLab = css(document.createElement('div'), 'font-size:12px;color:#666;margin-bottom:3px;');
    sceneLab.textContent = '话术场景';
    const sceneSel = css(document.createElement('select'),
      'width:100%;padding:5px;border:1px solid #d0d0d0;border-radius:6px;font-size:13px;background:#fff;color:#222;');
    Object.entries(CFG.scenes).forEach(([k, v]) => {
      const o = document.createElement('option');
      o.value = k; o.textContent = v.label;
      if (k === S.scene) o.selected = true;
      sceneSel.appendChild(o);
    });
    sceneSel.onchange = () => {
      S.scene = sceneSel.value;
      GM_setValue(sceneKey(SITE), S.scene);
      updateCounter();
      log('场景切换为 ' + sceneLabel(S.scene));
    };

    // 模式
    const modeLab = css(document.createElement('div'), 'font-size:12px;color:#666;margin:8px 0 3px;');
    modeLab.textContent = '单条发送模式';
    const modeRow = css(document.createElement('div'), 'display:flex;gap:6px;');
    const mkMode = (val, label, onBg) => {
      const b = css(document.createElement('button'),
        'flex:1;padding:6px;border:1px solid #d0d0d0;border-radius:6px;font-size:12px;cursor:pointer;background:#fff;color:#222;');
      b.textContent = label;
      b.dataset.val = val;
      b.onclick = () => {
        S.mode = val; GM_setValue('mode', val);
        paintModes();
        log('模式：' + (val === 'confirm' ? '逐条确认（只填不发）' : '自动发送'));
      };
      b.dataset.onbg = onBg || '#e8f5e9';
      return b;
    };
    modeRow.append(mkMode('confirm', '逐条确认'), mkMode('auto', '自动发送', '#fff3e0'));
    function paintModes() {
      [...modeRow.children].forEach(c => {
        const on = c.dataset.val === S.mode;
        c.style.background = on ? c.dataset.onbg : '#fff';
        c.style.borderColor = on ? '#66bb6a' : '#d0d0d0';
        c.style.fontWeight = on ? '600' : '400';
      });
    }

    const genBtn = btn('① 生成本条话术');
    genBtn.style.cssText += 'width:100%;background:#e3f2fd;';
    genBtn.onclick = () => { S.jd = ''; doCurrent(S.mode === 'auto'); };

    const againBtn = btn('换个说法重新生成');
    againBtn.style.cssText += 'width:100%;';
    againBtn.onclick = () => {
      if (!S.jd) return toast('还没有 JD');
      doCurrent(false);
    };

    const fillBtn = btn('② 立即填入并发送');
    fillBtn.style.cssText += 'width:100%;';
    fillBtn.onclick = async () => {
      const t = letterBox.value.trim();
      if (!t) return toast('还没有话术');
      try { await deliver(t, true); } catch (e) { log('✗ ' + e.message, 'err'); toast(e.message); }
    };

    const copyBtn = btn('复制话术'); copyBtn.style.flex = '1';
    copyBtn.onclick = () => { if (letterBox.value) { GM_setClipboard(letterBox.value, 'text'); toast('已复制'); } };
    const pasteBtn = btn('读剪贴板当JD'); pasteBtn.style.flex = '1';
    pasteBtn.onclick = async () => {
      try {
        const t = await navigator.clipboard.readText();
        if (!t || t.length < 40) return toast('剪贴板内容太短');
        S.jd = t; jdBox.value = t; toast('已读入 JD（' + t.length + ' 字）');
      } catch (e) { toast('读剪贴板失败，请手动粘贴到下面框里'); }
    };
    const dbgBtn = btn('调试导出'); dbgBtn.style.flex = '1';
    dbgBtn.onclick = debugDump;
    const row1 = css(document.createElement('div'), 'display:flex;gap:6px;margin-top:6px;');
    row1.append(copyBtn, pasteBtn, dbgBtn);

    // JD 兜底
    const jdLab = css(document.createElement('div'), 'font-size:12px;color:#666;margin:10px 0 3px;');
    jdLab.textContent = 'JD（自动读不到时，手动粘到这里）';
    const jdBox = css(document.createElement('textarea'),
      'width:100%;height:56px;padding:6px;border:1px solid #d0d0d0;border-radius:6px;' +
      'font-size:12px;resize:vertical;box-sizing:border-box;background:#fff;color:#222;');
    jdBox.placeholder = '把岗位 JD 粘到这里，再点①';
    jdBox.id = 'ai-job-jd';
    jdBox.oninput = () => { S.jd = jdBox.value.trim(); };

    // 话术
    const letterLab = css(document.createElement('div'), 'font-size:12px;color:#666;margin:10px 0 3px;');
    letterLab.textContent = '生成的话术（可直接改）';
    letterBox = css(document.createElement('textarea'),
      'width:100%;height:112px;padding:6px;border:1px solid #d0d0d0;border-radius:6px;' +
      'font-size:12px;resize:vertical;box-sizing:border-box;background:#fff;color:#222;');
    letterBox.placeholder = '点①生成';
    letterBox.id = 'ai-job-letter';
    letterBox.oninput = updateCounter;
    counterEl = css(document.createElement('div'), 'font-size:11px;color:#888;margin:2px 0 0;');

    // 日志
    const logLab = css(document.createElement('div'), 'font-size:12px;color:#666;margin:10px 0 3px;');
    logLab.textContent = '日志';
    logBoxEl = css(document.createElement('div'),
      'height:140px;overflow-y:auto;font-size:11px;background:#fafafa;border:1px solid #eee;' +
      'border-radius:6px;padding:5px;');
    logBoxEl.id = 'ai-job-log';
    // ⚠ 必须把 log() 写日志用的那个引用指过来。
    //   之前面板里建了日志框、也 append 进去了，但 log() 里的 logBox 一直是 null
    //   —— 结果所有日志只进了 Console，面板上永远是空的，用户根本看不到筛选理由。
    logBox = logBoxEl;

    // 设置
    const settings = css(document.createElement('details'), 'margin-top:10px;font-size:12px;color:#666;');
    const sum = document.createElement('summary');
    sum.textContent = '筛选与连发设置';
    sum.style.cursor = 'pointer';
    const setBox = css(document.createElement('div'), 'padding:6px 0;');
    const mkNum = (label, key, val, min, max, hint) => {
      const w = css(document.createElement('div'), 'display:flex;align-items:center;gap:6px;margin:4px 0;');
      const l = css(document.createElement('span'), 'flex:1;');
      l.textContent = label;
      if (hint) l.title = hint;
      const i = css(document.createElement('input'),
        'width:64px;padding:3px 5px;border:1px solid #d0d0d0;border-radius:4px;font-size:12px;background:#fff;color:#222;');
      i.type = 'number'; i.min = min; i.max = max; i.value = val;
      i.onchange = () => {
        S[key] = Math.max(min, Math.min(max, +i.value || val));
        GM_setValue(key, S[key]); refreshHud();
      };
      w.append(l, i);
      return w;
    };
    setBox.append(
      mkNum('投递门槛（匹配分 ≥）', 'threshold', S.threshold, 0, 100,
        '分数由本地 matcher.py 算出：只看标题/公司名排除，技能命中 + 职称加权 − 资历/学历/签证扣分'),
      mkNum('每日自动发送上限', 'cap', S.cap, 1, 150),
      mkNum('每条最小间隔（秒）', 'sendMin', S.sendMin, 5, 600),
      mkNum('每条最大间隔（秒）', 'sendMax', S.sendMax, 6, 900),
      mkNum('最多翻页数', 'maxPages', S.maxPages, 1, 10),
    );

    // 判定档位
    const bandLab = css(document.createElement('div'), 'font-size:12px;color:#666;margin:8px 0 3px;');
    bandLab.textContent = '投递判定档位';
    const bandRow = css(document.createElement('div'), 'display:flex;gap:6px;');
    const mkBand = (val, label) => {
      const b = css(document.createElement('button'),
        'flex:1;padding:6px;border:1px solid #d0d0d0;border-radius:6px;font-size:12px;cursor:pointer;background:#fff;color:#222;');
      b.textContent = label; b.dataset.val = val;
      b.onclick = () => { S.band = val; GM_setValue('band', val); paintBands(); refreshHud(); };
      return b;
    };
    bandRow.append(mkBand('apply', '只投高分'), mkBand('apply+review', '高分+边界都投'));
    function paintBands() {
      [...bandRow.children].forEach(c => {
        const on = c.dataset.val === S.band;
        c.style.background = on ? '#e3f2fd' : '#fff';
        c.style.borderColor = on ? '#64b5f6' : '#d0d0d0';
        c.style.fontWeight = on ? '600' : '400';
      });
    }

    const resetBtn = btn('清空「已投过」记录');
    resetBtn.style.cssText += 'width:100%;margin-top:6px;font-size:12px;';
    resetBtn.onclick = () => {
      if (!confirm(`确定清空已投记录吗？（当前 ${appliedCount()} 条）\n清空后可能会重复投递之前投过的岗位。`)) return;
      GM_setValue(APPLIED_KEY, []);
      log('已清空已投记录', 'ok');
      refreshHud();
    };

    setBox.append(bandLab, bandRow, resetBtn);
    settings.append(sum, setBox);

    // 站点操作说明
    const help = css(document.createElement('div'),
      'font-size:11px;color:#777;margin-top:10px;padding:6px 8px;background:#fffde7;border-radius:6px;');
    help.innerHTML = SITE && SITE.key === 'linkedin'
      ? '<b>自动档位</b>：先在领英「职位搜索」结果页（左栏一排岗位卡片），再点绿按钮。<br>'
        + '它会逐张卡片点开 → 读 JD → 判断值不值得投 → 值得就自动走 Easy Apply。<br>'
        + '遇到签证/工作许可类问题会主动退回人工（不替你编答案）。'
      : '<b>自动档位</b>：先在 BOSS 岗位列表页（左栏一排岗位卡片），再点绿按钮。<br>'
        + '它会逐张卡片点开 → 读 JD → 判断值不值得投 → 值得就点「立即沟通」并发招呼语。';

    body.append(hud, autoTitle, autoBtn, dryBtn, stopBtn, statsEl,
      manualTitle, sceneLab, sceneSel, modeLab, modeRow,
      css(document.createElement('div'), 'margin-top:8px;'),
      genBtn, againBtn, fillBtn, row1,
      jdLab, jdBox, letterLab, letterBox, counterEl, logLab, logBoxEl, settings, help);

    panel.append(head, body);
    document.body.appendChild(panel);
    paintModes();
    paintBands();
    updateCounter();
    refreshHud();
  }

  function refreshHud() {
    if (!hud) return;
    const st = S.running ? (S.dryRun ? '演练中…' : '自动投递中…')
      : (S.busy ? '生成中…' : '就绪');
    hud.innerHTML =
      `状态：<b>${st}</b>　｜　今日已投 <b>${sentToday()}</b> / ${S.cap}<br>` +
      `场景：${sceneLabel(S.scene)}<br>` +
      `筛选：≥${S.threshold} 分投 ｜ ${S.band === 'apply' ? '只投高分' : '高分+边界'}`
      + ` ｜ 已投库 ${appliedCount()} 条`;
    if (statsEl) {
      if (S.running && S.stats) {
        const s = S.stats;
        statsEl.textContent = `本轮：扫 ${s.scanned} ｜ 投 ${s.applied} ｜ 跳过 ${s.skipped}`
          + ` ｜ 待人工 ${s.manual} ｜ 失败 ${s.failed}`;
      } else if (S.stats) {
        const s = S.stats;
        statsEl.textContent = `上轮：扫 ${s.scanned} ｜ 投 ${s.applied} ｜ 跳过 ${s.skipped}`
          + ` ｜ 待人工 ${s.manual} ｜ 失败 ${s.failed}`;
      } else {
        statsEl.textContent = '点「先演练一遍」可以先看它打算投哪些岗位，不实际投。';
      }
    }
  }

  async function checkServer() {
    try {
      const r = await api('/health');
      if (r.ok) {
        statusDot.style.background = '#4caf50';
        log(`本地服务正常 ｜ 模型 ${r.model} ｜ 历史话术 ${r.history_count} 条`, 'ok');
        if (r.match) {
          log(`筛选引擎：≥${r.match.threshold} 分投 ｜ 硬排除词 ${r.match.hard_exclude_terms} 个`
            + ` ｜ 目标区域 ${(r.match.target_regions || []).join('/') || '不限'}`
            + ` ｜ 签证 ${r.match.need_visa ? '需要（不赞助的会扣分）' : '不需要'}`, 'ok');
          S.threshold = r.match.threshold;   // 以服务端 .env 为准
        }
        if (r.screening) {
          S.screening = r.screening;
          if (S.screening.auto_answer) {
            log(`资质自动回填：开 ｜ 需赞助 ${S.screening.require_sponsorship ? '是' : '否'}`
              + ` ｜ 国籍 ${S.screening.nationality || '-'}`, 'ok');
          } else {
            log('资质自动回填：关（签证/工作许可类问题一律退回人工）', 'ok');
          }
        }
        refreshHud();
      } else {
        statusDot.style.background = '#ff9800';
        log('本地服务在，但自检有问题：' + (r.problems || []).join('；'), 'err');
      }
    } catch (e) {
      statusDot.style.background = '#f44336';
      log(e.message, 'err');
    }
  }

  // ==================================================================
  // 启动
  // ==================================================================
  function boot() {
    if (!SITE) return;
    if (isVerifyPage()) {
      console.warn('[求职助手] 当前是安全验证页，脚本暂停。');
      return;
    }
    if (document.getElementById('ai-job-helper-panel')) return;  // 已挂载就不重复建
    buildPanel();
    log(`已加载（${SITE.name}）。先确认本地服务在跑：http://127.0.0.1:8765/health`);
    log(`本页识别到岗位卡片 ${jobCards().length} 个`);
    if (SITE.key === 'zhipin' && !isLoggedIn()) log('提示：没检测到登录态，可能登录已失效。', 'err');
    checkServer();
  }

  // SPA 页面切换后重新挂载
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      if (!S.running) S.jd = '';   // 自动流程跑动中别清，会把当前岗位的 JD 弄丢
      if (!document.body.contains(panel)) boot();
    }
  }, 1200);

  GM_registerMenuCommand('▶ 一键自动：筛完就投', () => autoRun(false));
  GM_registerMenuCommand('◐ 演练：只筛不投', () => autoRun(true));
  GM_registerMenuCommand('■ 停止', () => { S.running = false; S.busy = false; });
  GM_registerMenuCommand('显示/隐藏助手面板', () => {
    if (!panel) return boot();
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 1200));
  } else {
    setTimeout(boot, 1200);
  }
})();
